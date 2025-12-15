# exercises.py - Database Esercizi Lou
# Struttura scientifica basata su Bret Contreras, Dr. Mike Israetel, Jeff Nippard

EXERCISES = {
    # ==================== GLUTEI ====================
    "glutes": {
        "tier1": [  # Obbligatori - massima attivazione EMG
            {"id": "hip_thrust_barbell", "name": "Hip Thrust Bilanciere", "equipment": ["barbell"], "difficulty": 2, "primary": "glutes", "secondary": ["hamstrings"], "movement": "hip_dominant"},
            {"id": "hip_thrust_machine", "name": "Hip Thrust Machine", "equipment": ["machines"], "difficulty": 1, "primary": "glutes", "secondary": ["hamstrings"], "movement": "hip_dominant"},
            {"id": "glute_bridge_barbell", "name": "Glute Bridge Bilanciere", "equipment": ["barbell"], "difficulty": 1, "primary": "glutes", "secondary": ["hamstrings"], "movement": "hip_dominant"},
        ],
        "tier2": [  # Compound secondari
            {"id": "romanian_deadlift", "name": "Romanian Deadlift", "equipment": ["barbell", "dumbbells"], "difficulty": 2, "primary": "glutes", "secondary": ["hamstrings", "back"], "movement": "hinge"},
            {"id": "sumo_deadlift", "name": "Sumo Deadlift", "equipment": ["barbell"], "difficulty": 3, "primary": "glutes", "secondary": ["quads", "hamstrings"], "movement": "hinge"},
            {"id": "bulgarian_split_squat", "name": "Bulgarian Split Squat", "equipment": ["dumbbells", "barbell"], "difficulty": 2, "primary": "glutes", "secondary": ["quads"], "movement": "lunge"},
            {"id": "cable_pull_through", "name": "Cable Pull Through", "equipment": ["cables"], "difficulty": 1, "primary": "glutes", "secondary": ["hamstrings"], "movement": "hinge"},
            {"id": "good_morning", "name": "Good Morning", "equipment": ["barbell"], "difficulty": 2, "primary": "glutes", "secondary": ["hamstrings", "back"], "movement": "hinge"},
        ],
        "tier3": [  # Isolation - gluteo medio/piccolo
            {"id": "hip_abduction_machine", "name": "Hip Abduction Machine", "equipment": ["machines"], "difficulty": 1, "primary": "glutes", "secondary": [], "movement": "abduction"},
            {"id": "cable_hip_abduction", "name": "Cable Hip Abduction", "equipment": ["cables"], "difficulty": 1, "primary": "glutes", "secondary": [], "movement": "abduction"},
            {"id": "banded_clamshell", "name": "Clamshell con Banda", "equipment": ["bands"], "difficulty": 1, "primary": "glutes", "secondary": [], "movement": "abduction"},
            {"id": "cable_kickback", "name": "Cable Kickback", "equipment": ["cables"], "difficulty": 1, "primary": "glutes", "secondary": [], "movement": "extension"},
            {"id": "frog_pump", "name": "Frog Pump", "equipment": ["bodyweight"], "difficulty": 1, "primary": "glutes", "secondary": [], "movement": "hip_dominant"},
            {"id": "single_leg_hip_thrust", "name": "Single Leg Hip Thrust", "equipment": ["bodyweight", "barbell"], "difficulty": 2, "primary": "glutes", "secondary": ["hamstrings"], "movement": "hip_dominant"},
        ]
    },
    
    # ==================== QUADRICIPITI ====================
    "quads": {
        "tier1": [
            {"id": "back_squat", "name": "Back Squat", "equipment": ["barbell"], "difficulty": 3, "primary": "quads", "secondary": ["glutes"], "movement": "squat"},
            {"id": "front_squat", "name": "Front Squat", "equipment": ["barbell"], "difficulty": 3, "primary": "quads", "secondary": ["core"], "movement": "squat"},
            {"id": "leg_press", "name": "Leg Press", "equipment": ["machines"], "difficulty": 1, "primary": "quads", "secondary": ["glutes"], "movement": "squat"},
            {"id": "hack_squat", "name": "Hack Squat", "equipment": ["machines"], "difficulty": 2, "primary": "quads", "secondary": ["glutes"], "movement": "squat"},
        ],
        "tier2": [
            {"id": "goblet_squat", "name": "Goblet Squat", "equipment": ["dumbbells", "kettlebell"], "difficulty": 1, "primary": "quads", "secondary": ["glutes", "core"], "movement": "squat"},
            {"id": "lunges", "name": "Affondi", "equipment": ["dumbbells", "barbell", "bodyweight"], "difficulty": 2, "primary": "quads", "secondary": ["glutes"], "movement": "lunge"},
            {"id": "walking_lunges", "name": "Walking Lunges", "equipment": ["dumbbells", "bodyweight"], "difficulty": 2, "primary": "quads", "secondary": ["glutes"], "movement": "lunge"},
            {"id": "step_ups", "name": "Step Ups", "equipment": ["dumbbells", "barbell"], "difficulty": 2, "primary": "quads", "secondary": ["glutes"], "movement": "lunge"},
            {"id": "split_squat", "name": "Split Squat", "equipment": ["dumbbells", "bodyweight"], "difficulty": 2, "primary": "quads", "secondary": ["glutes"], "movement": "lunge"},
        ],
        "tier3": [
            {"id": "leg_extension", "name": "Leg Extension", "equipment": ["machines"], "difficulty": 1, "primary": "quads", "secondary": [], "movement": "isolation"},
            {"id": "sissy_squat", "name": "Sissy Squat", "equipment": ["bodyweight"], "difficulty": 3, "primary": "quads", "secondary": [], "movement": "isolation"},
        ]
    },
    
    # ==================== HAMSTRING/FEMORALI ====================
    "hamstrings": {
        "tier1": [
            {"id": "romanian_deadlift", "name": "Romanian Deadlift", "equipment": ["barbell", "dumbbells"], "difficulty": 2, "primary": "hamstrings", "secondary": ["glutes", "back"], "movement": "hinge"},
            {"id": "lying_leg_curl", "name": "Leg Curl Sdraiato", "equipment": ["machines"], "difficulty": 1, "primary": "hamstrings", "secondary": [], "movement": "curl"},
            {"id": "seated_leg_curl", "name": "Leg Curl Seduto", "equipment": ["machines"], "difficulty": 1, "primary": "hamstrings", "secondary": [], "movement": "curl"},
        ],
        "tier2": [
            {"id": "stiff_leg_deadlift", "name": "Stiff Leg Deadlift", "equipment": ["barbell", "dumbbells"], "difficulty": 2, "primary": "hamstrings", "secondary": ["glutes", "back"], "movement": "hinge"},
            {"id": "single_leg_rdl", "name": "Single Leg RDL", "equipment": ["dumbbells"], "difficulty": 3, "primary": "hamstrings", "secondary": ["glutes"], "movement": "hinge"},
            {"id": "nordic_curl", "name": "Nordic Curl", "equipment": ["bodyweight"], "difficulty": 3, "primary": "hamstrings", "secondary": [], "movement": "curl"},
            {"id": "glute_ham_raise", "name": "Glute Ham Raise", "equipment": ["machines"], "difficulty": 3, "primary": "hamstrings", "secondary": ["glutes"], "movement": "curl"},
        ],
        "tier3": [
            {"id": "single_leg_curl", "name": "Single Leg Curl", "equipment": ["machines"], "difficulty": 1, "primary": "hamstrings", "secondary": [], "movement": "curl"},
            {"id": "stability_ball_curl", "name": "Stability Ball Curl", "equipment": ["stability_ball"], "difficulty": 2, "primary": "hamstrings", "secondary": ["glutes"], "movement": "curl"},
        ]
    },
    
    # ==================== SCHIENA/DORSALI ====================
    "back": {
        "tier1": [
            {"id": "lat_pulldown", "name": "Lat Pulldown", "equipment": ["cables", "machines"], "difficulty": 1, "primary": "back", "secondary": ["biceps"], "movement": "vertical_pull"},
            {"id": "barbell_row", "name": "Rematore Bilanciere", "equipment": ["barbell"], "difficulty": 2, "primary": "back", "secondary": ["biceps"], "movement": "horizontal_pull"},
            {"id": "pull_ups", "name": "Trazioni", "equipment": ["bodyweight"], "difficulty": 3, "primary": "back", "secondary": ["biceps"], "movement": "vertical_pull"},
            {"id": "seated_cable_row", "name": "Seated Cable Row", "equipment": ["cables"], "difficulty": 1, "primary": "back", "secondary": ["biceps"], "movement": "horizontal_pull"},
        ],
        "tier2": [
            {"id": "dumbbell_row", "name": "Rematore Manubrio", "equipment": ["dumbbells"], "difficulty": 2, "primary": "back", "secondary": ["biceps"], "movement": "horizontal_pull"},
            {"id": "t_bar_row", "name": "T-Bar Row", "equipment": ["barbell"], "difficulty": 2, "primary": "back", "secondary": ["biceps"], "movement": "horizontal_pull"},
            {"id": "chin_ups", "name": "Chin Ups", "equipment": ["bodyweight"], "difficulty": 3, "primary": "back", "secondary": ["biceps"], "movement": "vertical_pull"},
            {"id": "chest_supported_row", "name": "Chest Supported Row", "equipment": ["dumbbells", "machines"], "difficulty": 1, "primary": "back", "secondary": ["biceps"], "movement": "horizontal_pull"},
        ],
        "tier3": [
            {"id": "face_pull", "name": "Face Pull", "equipment": ["cables"], "difficulty": 1, "primary": "back", "secondary": ["shoulders"], "movement": "pull"},
            {"id": "straight_arm_pulldown", "name": "Straight Arm Pulldown", "equipment": ["cables"], "difficulty": 1, "primary": "back", "secondary": [], "movement": "isolation"},
            {"id": "reverse_fly", "name": "Reverse Fly", "equipment": ["dumbbells", "cables", "machines"], "difficulty": 1, "primary": "back", "secondary": ["shoulders"], "movement": "isolation"},
        ]
    },
    
    # ==================== PETTO ====================
    "chest": {
        "tier1": [
            {"id": "bench_press", "name": "Panca Piana", "equipment": ["barbell"], "difficulty": 2, "primary": "chest", "secondary": ["shoulders", "triceps"], "movement": "horizontal_push"},
            {"id": "dumbbell_bench_press", "name": "Panca Piana Manubri", "equipment": ["dumbbells"], "difficulty": 2, "primary": "chest", "secondary": ["shoulders", "triceps"], "movement": "horizontal_push"},
            {"id": "incline_bench_press", "name": "Panca Inclinata", "equipment": ["barbell", "dumbbells"], "difficulty": 2, "primary": "chest", "secondary": ["shoulders", "triceps"], "movement": "incline_push"},
        ],
        "tier2": [
            {"id": "cable_fly", "name": "Croci ai Cavi", "equipment": ["cables"], "difficulty": 1, "primary": "chest", "secondary": [], "movement": "fly"},
            {"id": "dumbbell_fly", "name": "Croci Manubri", "equipment": ["dumbbells"], "difficulty": 2, "primary": "chest", "secondary": [], "movement": "fly"},
            {"id": "push_ups", "name": "Push Ups", "equipment": ["bodyweight"], "difficulty": 1, "primary": "chest", "secondary": ["shoulders", "triceps"], "movement": "horizontal_push"},
            {"id": "chest_press_machine", "name": "Chest Press Machine", "equipment": ["machines"], "difficulty": 1, "primary": "chest", "secondary": ["shoulders", "triceps"], "movement": "horizontal_push"},
        ],
        "tier3": [
            {"id": "pec_deck", "name": "Pec Deck", "equipment": ["machines"], "difficulty": 1, "primary": "chest", "secondary": [], "movement": "fly"},
        ]
    },
    
    # ==================== SPALLE ====================
    "shoulders": {
        "tier1": [
            {"id": "overhead_press", "name": "Shoulder Press", "equipment": ["barbell", "dumbbells"], "difficulty": 2, "primary": "shoulders", "secondary": ["triceps"], "movement": "vertical_push"},
            {"id": "lateral_raise", "name": "Alzate Laterali", "equipment": ["dumbbells", "cables"], "difficulty": 1, "primary": "shoulders", "secondary": [], "movement": "isolation"},
        ],
        "tier2": [
            {"id": "arnold_press", "name": "Arnold Press", "equipment": ["dumbbells"], "difficulty": 2, "primary": "shoulders", "secondary": ["triceps"], "movement": "vertical_push"},
            {"id": "cable_lateral_raise", "name": "Alzate Laterali Cavo", "equipment": ["cables"], "difficulty": 1, "primary": "shoulders", "secondary": [], "movement": "isolation"},
            {"id": "front_raise", "name": "Alzate Frontali", "equipment": ["dumbbells", "cables"], "difficulty": 1, "primary": "shoulders", "secondary": [], "movement": "isolation"},
            {"id": "upright_row", "name": "Tirate al Mento", "equipment": ["barbell", "cables"], "difficulty": 2, "primary": "shoulders", "secondary": ["biceps"], "movement": "pull"},
        ],
        "tier3": [
            {"id": "rear_delt_fly", "name": "Rear Delt Fly", "equipment": ["dumbbells", "cables", "machines"], "difficulty": 1, "primary": "shoulders", "secondary": ["back"], "movement": "isolation"},
            {"id": "face_pull", "name": "Face Pull", "equipment": ["cables"], "difficulty": 1, "primary": "shoulders", "secondary": ["back"], "movement": "pull"},
        ]
    },
    
    # ==================== BRACCIA ====================
    "arms": {
        "biceps": [
            {"id": "barbell_curl", "name": "Curl Bilanciere", "equipment": ["barbell"], "difficulty": 1, "primary": "biceps", "secondary": [], "movement": "curl"},
            {"id": "dumbbell_curl", "name": "Curl Manubri", "equipment": ["dumbbells"], "difficulty": 1, "primary": "biceps", "secondary": [], "movement": "curl"},
            {"id": "hammer_curl", "name": "Hammer Curl", "equipment": ["dumbbells"], "difficulty": 1, "primary": "biceps", "secondary": ["forearms"], "movement": "curl"},
            {"id": "incline_curl", "name": "Curl Inclinato", "equipment": ["dumbbells"], "difficulty": 2, "primary": "biceps", "secondary": [], "movement": "curl"},
            {"id": "preacher_curl", "name": "Preacher Curl", "equipment": ["barbell", "dumbbells"], "difficulty": 1, "primary": "biceps", "secondary": [], "movement": "curl"},
            {"id": "cable_curl", "name": "Curl ai Cavi", "equipment": ["cables"], "difficulty": 1, "primary": "biceps", "secondary": [], "movement": "curl"},
            {"id": "concentration_curl", "name": "Concentration Curl", "equipment": ["dumbbells"], "difficulty": 1, "primary": "biceps", "secondary": [], "movement": "curl"},
        ],
        "triceps": [
            {"id": "tricep_pushdown", "name": "Tricep Pushdown", "equipment": ["cables"], "difficulty": 1, "primary": "triceps", "secondary": [], "movement": "extension"},
            {"id": "overhead_tricep_extension", "name": "French Press", "equipment": ["dumbbells", "cables", "barbell"], "difficulty": 2, "primary": "triceps", "secondary": [], "movement": "extension"},
            {"id": "skull_crushers", "name": "Skull Crushers", "equipment": ["barbell", "dumbbells"], "difficulty": 2, "primary": "triceps", "secondary": [], "movement": "extension"},
            {"id": "tricep_dips", "name": "Dips Tricipiti", "equipment": ["bodyweight"], "difficulty": 2, "primary": "triceps", "secondary": ["chest", "shoulders"], "movement": "push"},
            {"id": "close_grip_bench", "name": "Panca Presa Stretta", "equipment": ["barbell"], "difficulty": 2, "primary": "triceps", "secondary": ["chest"], "movement": "push"},
            {"id": "tricep_kickback", "name": "Tricep Kickback", "equipment": ["dumbbells", "cables"], "difficulty": 1, "primary": "triceps", "secondary": [], "movement": "extension"},
        ]
    },
    
    # ==================== ADDOMINALI ====================
    "abs": {
        "tier1": [
            {"id": "cable_crunch", "name": "Cable Crunch", "equipment": ["cables"], "difficulty": 1, "primary": "abs", "secondary": [], "movement": "flexion"},
            {"id": "hanging_leg_raise", "name": "Leg Raise Sospeso", "equipment": ["bodyweight"], "difficulty": 3, "primary": "abs", "secondary": [], "movement": "flexion"},
            {"id": "ab_wheel", "name": "Ab Wheel Rollout", "equipment": ["ab_wheel"], "difficulty": 3, "primary": "abs", "secondary": [], "movement": "anti_extension"},
        ],
        "tier2": [
            {"id": "plank", "name": "Plank", "equipment": ["bodyweight"], "difficulty": 1, "primary": "abs", "secondary": [], "movement": "anti_extension"},
            {"id": "dead_bug", "name": "Dead Bug", "equipment": ["bodyweight"], "difficulty": 1, "primary": "abs", "secondary": [], "movement": "anti_extension"},
            {"id": "pallof_press", "name": "Pallof Press", "equipment": ["cables", "bands"], "difficulty": 2, "primary": "abs", "secondary": [], "movement": "anti_rotation"},
            {"id": "russian_twist", "name": "Russian Twist", "equipment": ["bodyweight", "dumbbells"], "difficulty": 2, "primary": "abs", "secondary": [], "movement": "rotation"},
        ],
        "tier3": [
            {"id": "crunch", "name": "Crunch", "equipment": ["bodyweight"], "difficulty": 1, "primary": "abs", "secondary": [], "movement": "flexion"},
            {"id": "bicycle_crunch", "name": "Bicycle Crunch", "equipment": ["bodyweight"], "difficulty": 1, "primary": "abs", "secondary": [], "movement": "flexion"},
            {"id": "leg_raise", "name": "Leg Raise", "equipment": ["bodyweight"], "difficulty": 2, "primary": "abs", "secondary": [], "movement": "flexion"},
            {"id": "mountain_climbers", "name": "Mountain Climbers", "equipment": ["bodyweight"], "difficulty": 1, "primary": "abs", "secondary": [], "movement": "dynamic"},
        ]
    }
}


def get_exercises_for_muscle(muscle, equipment_available, tier=None):
    """
    Ritorna esercizi per un muscolo specifico filtrati per equipaggiamento
    
    Args:
        muscle: 'glutes', 'quads', 'hamstrings', 'back', 'chest', 'shoulders', 'abs'
        equipment_available: lista es. ['barbell', 'dumbbells', 'cables', 'machines']
        tier: 'tier1', 'tier2', 'tier3' o None per tutti
    """
    if muscle not in EXERCISES:
        return []
    
    muscle_data = EXERCISES[muscle]
    
    # Arms ha struttura diversa
    if muscle == 'arms':
        result = []
        for sub in ['biceps', 'triceps']:
            for ex in muscle_data[sub]:
                if any(eq in equipment_available for eq in ex['equipment']):
                    result.append(ex)
        return result
    
    # Altri muscoli hanno tier1, tier2, tier3
    result = []
    tiers = [tier] if tier else ['tier1', 'tier2', 'tier3']
    
    for t in tiers:
        if t in muscle_data:
            for ex in muscle_data[t]:
                if any(eq in equipment_available for eq in ex['equipment']):
                    result.append(ex)
    
    return result


def get_all_exercise_ids():
    """Ritorna lista di tutti gli ID esercizi"""
    ids = []
    for muscle, data in EXERCISES.items():
        if muscle == 'arms':
            for sub in ['biceps', 'triceps']:
                ids.extend([ex['id'] for ex in data[sub]])
        else:
            for tier in ['tier1', 'tier2', 'tier3']:
                if tier in data:
                    ids.extend([ex['id'] for ex in data[tier]])
    return ids


def get_exercise_by_id(exercise_id):
    """Trova un esercizio per ID"""
    for muscle, data in EXERCISES.items():
        if muscle == 'arms':
            for sub in ['biceps', 'triceps']:
                for ex in data[sub]:
                    if ex['id'] == exercise_id:
                        return ex
        else:
            for tier in ['tier1', 'tier2', 'tier3']:
                if tier in data:
                    for ex in data[tier]:
                        if ex['id'] == exercise_id:
                            return ex
    return None


def get_exercise_name(exercise_id):
    """Ritorna il nome di un esercizio dato l'ID"""
    ex = get_exercise_by_id(exercise_id)
    return ex['name'] if ex else exercise_id


def select_exercises_for_day(
    target_muscles,
    equipment_available,
    favorite_exercises=None,
    excluded_exercises=None,
    day_type='hypertrophy',  # 'strength', 'hypertrophy', 'metabolic'
    num_exercises=4,
    week_number=1
):
    """
    Seleziona esercizi per un giorno secondo logica scientifica
    
    Args:
        target_muscles: lista muscoli target es. ['glutes', 'quads']
        equipment_available: es. ['barbell', 'dumbbells', 'cables']
        favorite_exercises: lista ID esercizi preferiti dall'utente
        excluded_exercises: lista ID esercizi da escludere
        day_type: tipo giorno per rep range
        num_exercises: quanti esercizi selezionare
        week_number: settimana corrente (per rotazione)
    
    Returns:
        lista di esercizi selezionati con set/rep appropriati
    """
    import random
    
    favorite_exercises = favorite_exercises or []
    excluded_exercises = excluded_exercises or []
    
    selected = []
    used_movements = set()
    
    # REP RANGES per tipo giorno
    rep_ranges = {
        'strength': {'min': 4, 'max': 6, 'rpe': 8, 'rest': 180},
        'hypertrophy': {'min': 8, 'max': 12, 'rpe': 7, 'rest': 90},
        'metabolic': {'min': 15, 'max': 20, 'rpe': 7, 'rest': 45}
    }
    
    day_config = rep_ranges.get(day_type, rep_ranges['hypertrophy'])
    
    for muscle in target_muscles:
        available = get_exercises_for_muscle(muscle, equipment_available)
        available = [ex for ex in available if ex['id'] not in excluded_exercises]
        
        if not available:
            continue
        
        # Priorità: tier1 > preferiti > tier2 > tier3
        tier1 = [ex for ex in available if get_exercise_by_id(ex['id']) and 
                 any(ex['id'] in EXERCISES.get(muscle, {}).get('tier1', []) for muscle in target_muscles)]
        favorites_available = [ex for ex in available if ex['id'] in favorite_exercises]
        
        # Seleziona almeno 1 tier1 per muscolo principale
        if tier1 and muscle in ['glutes', 'quads', 'back', 'chest']:
            # Rotazione: usa week_number per variare
            idx = (week_number - 1) % len(tier1)
            ex = tier1[idx]
            if ex['movement'] not in used_movements or muscle == 'glutes':
                selected.append({
                    **ex,
                    'sets': 4 if day_type == 'strength' else 3,
                    'reps_min': day_config['min'],
                    'reps_max': day_config['max'],
                    'rpe_target': day_config['rpe'],
                    'rest_seconds': day_config['rest']
                })
                used_movements.add(ex['movement'])
        
        # Aggiungi preferiti se disponibili
        for fav in favorites_available:
            if len(selected) >= num_exercises:
                break
            if fav['id'] not in [s['id'] for s in selected]:
                if fav['movement'] not in used_movements or len(selected) < 2:
                    selected.append({
                        **fav,
                        'sets': 3,
                        'reps_min': day_config['min'],
                        'reps_max': day_config['max'],
                        'rpe_target': day_config['rpe'],
                        'rest_seconds': day_config['rest']
                    })
                    used_movements.add(fav['movement'])
    
    # Riempi con altri esercizi se necessario
    while len(selected) < num_exercises:
        for muscle in target_muscles:
            if len(selected) >= num_exercises:
                break
            available = get_exercises_for_muscle(muscle, equipment_available)
            available = [ex for ex in available 
                        if ex['id'] not in [s['id'] for s in selected]
                        and ex['id'] not in excluded_exercises]
            
            if available:
                # Rotazione con week_number
                idx = (week_number - 1 + len(selected)) % len(available)
                ex = available[idx]
                selected.append({
                    **ex,
                    'sets': 3,
                    'reps_min': day_config['min'],
                    'reps_max': day_config['max'],
                    'rpe_target': day_config['rpe'],
                    'rest_seconds': day_config['rest']
                })
        
        # Evita loop infinito
        if len(selected) == 0:
            break
    
    return selected[:num_exercises]


def get_exercises_for_ui(equipment_available=None):
    """
    Ritorna tutti gli esercizi formattati per UI di selezione preferiti
    Raggruppati per muscolo
    """
    equipment_available = equipment_available or ['barbell', 'dumbbells', 'cables', 'machines', 'bodyweight']
    
    result = {}
    
    for muscle in ['glutes', 'quads', 'hamstrings', 'back', 'chest', 'shoulders', 'arms', 'abs']:
        exercises = get_exercises_for_muscle(muscle, equipment_available)
        if exercises:
            result[muscle] = [
                {'id': ex['id'], 'name': ex['name'], 'difficulty': ex.get('difficulty', 1)}
                for ex in exercises
            ]
    
    return result