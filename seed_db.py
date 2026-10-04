import sys
from google.cloud import firestore

# IMPORTANT: Hardcoded Project ID string to ensure compatibility with Agent Platform
PROJECT_ID = "qwiklabs-gcp-03-faea05ff8ea7"

def seed_firestore():
    print(f"Connecting to Firestore for project '{PROJECT_ID}'...")
    db = firestore.Client(project=PROJECT_ID)

    exercises = [
        {
            "id": "ex_001",
            "name": "Barbell Bench Press",
            "target_muscle": "Chest",
            "equipment": "Barbell, Bench",
            "difficulty": "Intermediate",
            "description": "Lie flat on a bench and press the barbell upward until arms are fully extended.",
            "default_sets": 4,
            "default_reps": 8,
        },
        {
            "id": "ex_002",
            "name": "Barbell Squat",
            "target_muscle": "Legs (Quadriceps, Glutes)",
            "equipment": "Barbell, Squat Rack",
            "difficulty": "Intermediate",
            "description": "Rest barbell across upper back, bend knees to lower hips until thighs are parallel to floor.",
            "default_sets": 4,
            "default_reps": 8,
        },
        {
            "id": "ex_003",
            "name": "Pull-ups",
            "target_muscle": "Back (Lats)",
            "equipment": "Pull-up Bar",
            "difficulty": "Intermediate",
            "description": "Grip bar palms facing away, pull body up until chin clears the bar.",
            "default_sets": 3,
            "default_reps": 10,
        },
        {
            "id": "ex_004",
            "name": "Dumbbell Shoulder Press",
            "target_muscle": "Shoulders (Deltoids)",
            "equipment": "Dumbbells, Bench",
            "difficulty": "Beginner",
            "description": "Sit upright with dumbbells at shoulder height and press straight overhead.",
            "default_sets": 3,
            "default_reps": 12,
        },
        {
            "id": "ex_005",
            "name": "Romanian Deadlift",
            "target_muscle": "Hamstrings, Glutes",
            "equipment": "Barbell or Dumbbells",
            "difficulty": "Intermediate",
            "description": "Hinge at hips with slight knee bend, lower weight along shins, then drive hips forward.",
            "default_sets": 3,
            "default_reps": 10,
        },
    ]

    print("Seeding 'exercises' collection...")
    exercises_ref = db.collection("exercises")
    for item in exercises:
        doc_id = item["id"]
        exercises_ref.document(doc_id).set(item)
        print(f"  ✓ Added exercise: {item['name']} ({doc_id})")

    print("\nSeeding sample 'workout_logs' collection...")
    logs_ref = db.collection("workout_logs")
    sample_log = {
        "user_id": "user_001",
        "exercise_name": "Barbell Bench Press",
        "sets": 4,
        "reps": 8,
        "weight_lbs": 185.0,
        "timestamp": firestore.SERVER_TIMESTAMP,
        "notes": "Felt strong today, hit all reps smoothly."
    }
    logs_ref.add(sample_log)
    print("  ✓ Added sample workout log")

    print("\nFirestore seeding complete! ✅")

if __name__ == "__main__":
    seed_firestore()
