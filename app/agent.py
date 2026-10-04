import asyncio
import datetime
import json
import os
import re
import time
import urllib.request
from zoneinfo import ZoneInfo

from a2ui.basic_catalog.provider import BasicCatalog
from a2ui.schema.manager import A2uiSchemaManager
from google import genai
from google.cloud import firestore, storage

from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.apps import App
from google.adk.code_executors.agent_engine_sandbox_code_executor import AgentEngineSandboxCodeExecutor
from google.adk.memory import VertexAiMemoryBankService
from google.adk.models import Gemini
from google.adk.tools import ToolContext
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from google.genai import types

from app.a2ui_utils import a2ui_callback

# IMPORTANT: Hardcode project ID as a string for Firestore compatibility on Agent Platform
FIRESTORE_PROJECT_ID = "qwiklabs-gcp-03-faea05ff8ea7"

# Hardcoded Cloud Storage bucket for media uploads
GCS_BUCKET_NAME = "fitcoach-ai-media-qwiklabs-gcp-03-faea05ff8ea7"

# Initialize Firestore client with hardcoded project ID
db = firestore.Client(project=FIRESTORE_PROJECT_ID)

# Load Agent Engine resource name from deployment_metadata.json for Agent Engine Sandbox Code Execution
agent_engine_resource_name = None
metadata_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "deployment_metadata.json")
if os.path.exists(metadata_path):
    try:
        with open(metadata_path, "r") as f:
            metadata = json.load(f)
            agent_engine_resource_name = metadata.get("remote_agent_runtime_id")
    except Exception as e:
        print(f"Warning: Failed to load deployment_metadata.json: {e}")

code_executor = None
if agent_engine_resource_name:
    code_executor = AgentEngineSandboxCodeExecutor(
        agent_engine_resource_name=agent_engine_resource_name
    )


# Memory callback: Extracts and persists user facts, preferences, and allergies across sessions
async def generate_memories_callback(callback_context: CallbackContext):
    try:
        await callback_context.add_session_to_memory()
    except Exception as e:
        print(f"Memory bank callback notice: {e}")
    return None


def memory_bank_service_builder():
    agent_engine_id = agent_engine_resource_name.split("/")[-1] if agent_engine_resource_name else None
    return VertexAiMemoryBankService(
        project=FIRESTORE_PROJECT_ID,
        location="us-central1",
        agent_engine_id=agent_engine_id,
    )


# A2UI Schema Manager setup (Version 0.8)
schema_manager = A2uiSchemaManager(
    version="0.8",
    catalogs=[BasicCatalog.get_config("0.8")],
)

a2ui_instruction = schema_manager.generate_system_prompt(
    role_description="You are FitCoach AI, an intelligent fitness & workout assistant. You help users find exercises from your Firestore database or online public exercise libraries, view exercise techniques, generate visual illustrations for workouts or meals, calculate estimated 1-Rep Max and target weights, log workout sessions, and review training history.",
    workflow_description="Analyze the user request, call appropriate tools when needed, and return structured A2UI visual cards when appropriate.",
    ui_description=(
        "Keep every surface tiny and flat: ONE Card > ONE Column > a few Text rows. "
        "Never nest a Card inside a Card. "
        "Use ONLY these components: Card, Column, Row, Text, and Image. Do not use "
        "Table or Heading (unsupported), or Buttons, actions, or forms (they do "
        "nothing in adk web). "
        "You may include one Image component, but only when you have a public https "
        "URL for the image (for example the URL an image tool returns after uploading "
        "to a public bucket). Set the Image url to that exact https link, for example "
        '{"Image": {"url": {"literalString": "https://..."}}}. Never point an '
        "Image at a bare filename, an artifact name, or a non-http(s) path. If you do "
        "not have a public URL, add a short Text line noting the image instead. "
        "No markdown in text; use the usageHint property ('h1', 'h2', 'body') for "
        "headings and emphasis. "
        "Output ONLY the raw A2UI JSON array — no prose, and never wrap it in "
        "<a2a_datapart_json> tags or 'kind'/'data'/'metadata' objects."
    ),
    include_schema=True,
    include_examples=True,
)

system_instruction = f"""{a2ui_instruction}

CROSS-SESSION MEMORY INSTRUCTIONS:
• Automatically recall and apply ALL stored user preferences across sessions, including:
  - Exercise preferences (favorite exercises, disliked movements, preferred equipment like dumbbells vs barbells)
  - Training goals, workout split preferences, injuries or joint limitations, and target muscle focus
  - Dietary restrictions, allergies, and nutritional preferences
• Always prioritize the user's remembered exercise preferences when recommending workouts or suggesting exercise alternatives.
• Use python code execution when performing custom math or calculations.
"""


def search_exercises(muscle_group: str = "") -> str:
    """Searches the Firestore database for exercises, optionally filtering by target muscle group.

    Args:
        muscle_group: Optional target muscle group (e.g. 'Chest', 'Legs', 'Back', 'Shoulders').

    Returns:
        A list of matching exercise names, target muscles, equipment, and recommended sets/reps.
    """
    try:
        ref = db.collection("exercises")
        docs = ref.stream()
        results = []
        for doc in docs:
            data = doc.to_dict()
            if not muscle_group or muscle_group.lower() in data.get("target_muscle", "").lower():
                results.append(
                    f"• {data.get('name')} (ID: {doc.id}) - Target: {data.get('target_muscle')} | Equipment: {data.get('equipment')} | Recommended: {data.get('default_sets')} sets x {data.get('default_reps')} reps"
                )
        if not results:
            return f"No exercises found for muscle group '{muscle_group}'."
        return f"Found {len(results)} exercise(s):\n" + "\n".join(results)
    except Exception as e:
        return f"Error querying Firestore exercises: {str(e)}"


def get_exercise_details(exercise_name: str) -> str:
    """Gets detailed instructions and equipment required for a specific exercise from Firestore.

    Args:
        exercise_name: The name or partial name of the exercise (e.g. 'Bench Press', 'Squat').

    Returns:
        Detailed exercise specifications, difficulty level, equipment, and step-by-step instructions.
    """
    try:
        ref = db.collection("exercises")
        docs = ref.stream()
        for doc in docs:
            data = doc.to_dict()
            if exercise_name.lower() in data.get("name", "").lower():
                return (
                    f"Exercise: {data.get('name')}\n"
                    f"Target Muscle: {data.get('target_muscle')}\n"
                    f"Difficulty: {data.get('difficulty')}\n"
                    f"Equipment Needed: {data.get('equipment')}\n"
                    f"Default Protocol: {data.get('default_sets')} sets of {data.get('default_reps')} reps\n"
                    f"Description: {data.get('description')}"
                )
        return f"Exercise '{exercise_name}' not found in database."
    except Exception as e:
        return f"Error retrieving exercise details: {str(e)}"


def fetch_online_exercise_library(search_query: str = "") -> str:
    """Fetches exercise descriptions and techniques from the public Wger Exercise API library.

    Args:
        search_query: Optional search keyword to filter exercises (e.g. 'squat', 'pulldown', 'bench').

    Returns:
        A list of exercises with names and instructions retrieved live from the online library.
    """
    try:
        url = "https://wger.de/api/v2/exercise-translation/?language=2&limit=40"
        api_key = os.getenv("WGER_API_KEY")
        headers = {"User-Agent": "FitCoachAI/1.0"}
        if api_key:
            headers["Authorization"] = f"Token {api_key}"

        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode())

        results = []
        for item in data.get("results", []):
            name = item.get("name", "")
            desc_raw = item.get("description", "")
            desc_clean = re.sub(r"<[^>]+>", "", desc_raw).strip()
            if not search_query or search_query.lower() in name.lower() or search_query.lower() in desc_clean.lower():
                short_desc = (desc_clean[:200] + "...") if len(desc_clean) > 200 else desc_clean
                results.append(f"• {name}: {short_desc}")

        if not results:
            return f"No online exercises found matching '{search_query}'."
        return f"Online Exercise Library Results ({len(results)} found):\n" + "\n".join(results[:5])
    except Exception as e:
        return f"Error fetching online exercise library: {str(e)}"


async def generate_fitness_image(prompt: str, tool_context: ToolContext) -> str:
    """Generates an image or illustration for an exercise, workout diagram, or healthy meal using Gemini image generation.

    Args:
        prompt: Description of the fitness visual to generate (e.g., 'dumbbell bench press form', 'protein power bowl').
        tool_context: Context automatically provided by ADK to manage session artifacts.

    Returns:
        The public HTTPS URL (https://storage.googleapis.com/<bucket>/<object>) of the generated image.
    """
    try:
        genai_client = genai.Client(vertexai=True, project=FIRESTORE_PROJECT_ID, location="global")
        response = genai_client.models.generate_content(
            model="gemini-3.1-flash-lite-image",
            contents=f"High quality fitness illustration: {prompt}",
            config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
        )

        part = response.candidates[0].content.parts[0]
        image_bytes = part.inline_data.data
        mime_type = part.inline_data.mime_type or "image/jpeg"

        ext = "png" if "png" in mime_type else "jpg"
        filename = f"fitness_image_{int(time.time())}.{ext}"

        # 1. Save artifact to Playground Artifacts panel
        artifact_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
        await tool_context.save_artifact(filename=filename, artifact=artifact_part)

        # 2. Upload same image bytes to public Cloud Storage bucket
        storage_client = storage.Client(project=FIRESTORE_PROJECT_ID)
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(filename)
        blob.upload_from_string(image_bytes, content_type=mime_type)

        public_url = f"https://storage.googleapis.com/{GCS_BUCKET_NAME}/{filename}"
        return public_url
    except Exception as e:
        return f"Error generating image: {str(e)}"


async def generate_fitness_video(prompt: str, tool_context: ToolContext) -> str:
    """Generates a short exercise demonstration video for an item in the agent's fitness domain using Google's Omni model (gemini-omni-flash-preview) in the global region.

    Args:
        prompt: Description of the fitness video to generate (e.g., 'A fitness coach demonstrating proper squat form').
        tool_context: Context automatically provided by ADK to manage session artifacts.

    Returns:
        The public HTTPS URL (https://storage.googleapis.com/<bucket>/<object>) of the generated video.
    """
    try:
        mime_type = "video/mp4"
        filename = f"fitness_video_{int(time.time())}.mp4"

        try:
            genai_client = genai.Client(vertexai=True, project=FIRESTORE_PROJECT_ID, location="global")
            operation = genai_client.models.generate_videos(
                model="gemini-omni-flash-preview",
                prompt=f"A high quality short video demonstrating: {prompt}",
                config=types.GenerateVideosConfig(aspect_ratio="16:9"),
            )
        except Exception:
            genai_client = genai.Client(vertexai=True, project=FIRESTORE_PROJECT_ID, location="us-central1")
            operation = genai_client.models.generate_videos(
                model="veo-3.1-fast-generate-001",
                prompt=f"A high quality short video demonstrating: {prompt}",
                config=types.GenerateVideosConfig(aspect_ratio="16:9"),
            )

        while not operation.done:
            await asyncio.sleep(5)
            operation = genai_client.operations.get(operation)

        if not operation.response or not operation.response.generated_videos:
            return "Error: Video generation completed but returned no video output."

        generated_video = operation.response.generated_videos[0]
        video_bytes = getattr(generated_video.video, "video_bytes", None)
        if not video_bytes:
            video_bytes = genai_client.files.download(file=generated_video.video)

        # 1. Save artifact with tool_context.save_artifact so it shows up in Playground's Artifacts panel
        artifact_part = types.Part.from_bytes(data=video_bytes, mime_type=mime_type)
        await tool_context.save_artifact(filename=filename, artifact=artifact_part)

        # 2. Upload same video bytes to the public Cloud Storage bucket and return public HTTPS URL
        storage_client = storage.Client(project=FIRESTORE_PROJECT_ID)
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(filename)
        blob.upload_from_string(video_bytes, content_type=mime_type)

        public_url = f"https://storage.googleapis.com/{GCS_BUCKET_NAME}/{filename}"
        return public_url
    except Exception as e:
        return f"Error generating video: {str(e)}"


def log_workout(exercise_name: str, sets: int, reps: int, weight_lbs: float, notes: str = "") -> str:
    """Logs a completed exercise session to the Firestore workout_logs collection.

    Args:
        exercise_name: Name of the exercise performed (e.g. 'Barbell Bench Press').
        sets: Number of completed sets.
        reps: Number of repetitions per set.
        weight_lbs: Weight lifted in pounds.
        notes: Optional notes on performance or effort.

    Returns:
        Confirmation string indicating the workout was saved to Firestore.
    """
    try:
        log_entry = {
            "user_id": "user_001",
            "exercise_name": exercise_name,
            "sets": sets,
            "reps": reps,
            "weight_lbs": weight_lbs,
            "timestamp": firestore.SERVER_TIMESTAMP,
            "notes": notes,
        }
        doc_ref = db.collection("workout_logs").add(log_entry)
        return f"Successfully logged workout for {exercise_name} ({sets} sets x {reps} reps @ {weight_lbs} lbs) in Firestore!"
    except Exception as e:
        return f"Error saving workout log to Firestore: {str(e)}"


def get_recent_workout_logs(limit: int = 5) -> str:
    """Fetches recent workout history from the Firestore workout_logs collection.

    Args:
        limit: Maximum number of recent workout entries to retrieve.

    Returns:
        Formatted summary of recent workout sessions.
    """
    try:
        logs_ref = db.collection("workout_logs").limit(limit)
        docs = logs_ref.stream()
        entries = []
        for doc in docs:
            d = doc.to_dict()
            entries.append(
                f"• {d.get('exercise_name')}: {d.get('sets')} sets x {d.get('reps')} reps @ {d.get('weight_lbs')} lbs (Notes: '{d.get('notes', '')}')"
            )
        if not entries:
            return "No recent workout logs found."
        return "Recent Workout History:\n" + "\n".join(entries)
    except Exception as e:
        return f"Error fetching workout logs: {str(e)}"


def calculate_one_rep_max(weight_lbs: float, reps: int) -> str:
    """Calculates the estimated 1-Rep Max (1RM) and training load targets based on weight lifted and reps.

    Args:
        weight_lbs: The weight lifted in pounds.
        reps: The number of repetitions completed.

    Returns:
        Estimated 1RM along with target weights for strength (85%), hypertrophy (70%), and endurance (60%).
    """
    if reps <= 0 or weight_lbs <= 0:
        return "Please provide positive numbers for weight and reps."
    if reps == 1:
        one_rm = weight_lbs
    else:
        one_rm = round(weight_lbs * (1 + reps / 30.0), 1)

    strength_85 = round(one_rm * 0.85, 1)
    hypertrophy_70 = round(one_rm * 0.70, 1)
    endurance_60 = round(one_rm * 0.60, 1)

    return (
        f"Estimated 1-Rep Max (1RM): {one_rm} lbs\n"
        f"Target Training Loads:\n"
        f"• Heavy Strength (85% 1RM ~ 5-6 reps): {strength_85} lbs\n"
        f"• Hypertrophy (70% 1RM ~ 8-12 reps): {hypertrophy_70} lbs\n"
        f"• Endurance (60% 1RM ~ 15+ reps): {endurance_60} lbs"
    )


root_agent = Agent(
    name="root_agent",
    model=Gemini(
        model="gemini-2.5-flash",
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=system_instruction,
    tools=[PreloadMemoryTool(), search_exercises, get_exercise_details, fetch_online_exercise_library, generate_fitness_image, generate_fitness_video, calculate_one_rep_max, log_workout, get_recent_workout_logs],
    code_executor=code_executor,
    after_agent_callback=generate_memories_callback,
    after_model_callback=a2ui_callback,
)

app = App(
    root_agent=root_agent,
    name="app",
)






