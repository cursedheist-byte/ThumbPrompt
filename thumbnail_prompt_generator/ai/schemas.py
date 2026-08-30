"""
Expected JSON shapes for the two Gemini calls, plus lightweight
validation helpers. Kept schema-first so prompt_engine.py can both
(a) tell the model exactly what to return and (b) verify what comes
back before it's trusted by the rest of the app.
"""

CONCEPT_KEYS = [
    "concept_name",
    "core_visual_idea",
    "composition",
    "subject_placement",
    "camera",
    "lighting",
    "background",
    "emotional_hook",
    "important_objects",
    "why_it_could_work",
]

CONCEPTS_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "video_understanding": {
            "type": "string",
            "description": "One or two sentences on what the video is about and why it's interesting.",
        },
        "concepts": {
            "type": "array",
            "minItems": 3,
            "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "concept_name": {"type": "string"},
                    "core_visual_idea": {"type": "string"},
                    "composition": {"type": "string"},
                    "subject_placement": {"type": "string"},
                    "camera": {"type": "string"},
                    "lighting": {"type": "string"},
                    "background": {"type": "string"},
                    "emotional_hook": {"type": "string"},
                    "important_objects": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "why_it_could_work": {"type": "string"},
                },
                "required": CONCEPT_KEYS,
            },
        },
    },
    "required": ["concepts"],
}

FINAL_PROMPT_KEYS = [
    "visual_style",
    "composition",
    "camera_angle",
    "camera_distance",
    "subject_placement",
    "character_appearance",
    "facial_expression",
    "body_pose",
    "important_objects",
    "object_scale",
    "foreground",
    "background",
    "lighting",
    "color_relationships",
    "depth_and_atmosphere",
    "visual_hierarchy",
    "storytelling",
    "emotional_impact",
    "readability_at_small_size",
    "reference_image_instructions",
    "preserve_from_reference",
    "do_not_copy_from_reference",
    "negative_instructions",
    "text_instructions",
]

FINAL_PROMPT_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "final_image_prompt": {
            "type": "string",
            "description": "The complete, ready-to-paste prompt for an image generator, written as flowing prose that incorporates every field below.",
        },
        "structured_breakdown": {
            "type": "object",
            "properties": {key: {"type": "string"} for key in FINAL_PROMPT_KEYS},
            "required": FINAL_PROMPT_KEYS,
        },
    },
    "required": ["final_image_prompt", "structured_breakdown"],
}


class SchemaValidationError(Exception):
    pass


def validate_concepts_response(data: dict):
    if not isinstance(data, dict):
        raise SchemaValidationError("Response is not a JSON object.")
    concepts = data.get("concepts")
    if not isinstance(concepts, list) or len(concepts) == 0:
        raise SchemaValidationError("Response is missing a non-empty 'concepts' list.")
    # Be lenient on count (model sometimes returns 2-4) but require the
    # core fields on each concept so the UI never breaks.
    for i, concept in enumerate(concepts):
        if not isinstance(concept, dict):
            raise SchemaValidationError(f"Concept {i} is not an object.")
        missing = [k for k in CONCEPT_KEYS if k not in concept]
        if missing:
            raise SchemaValidationError(
                f"Concept {i} is missing required fields: {missing}"
            )
    return True


def validate_final_prompt_response(data: dict):
    if not isinstance(data, dict):
        raise SchemaValidationError("Response is not a JSON object.")
    if not data.get("final_image_prompt") or not isinstance(
        data.get("final_image_prompt"), str
    ):
        raise SchemaValidationError("Response is missing a non-empty 'final_image_prompt' string.")
    breakdown = data.get("structured_breakdown")
    if not isinstance(breakdown, dict):
        raise SchemaValidationError("Response is missing 'structured_breakdown'.")
    missing = [k for k in FINAL_PROMPT_KEYS if k not in breakdown]
    if missing:
        raise SchemaValidationError(
            f"structured_breakdown is missing required fields: {missing}"
        )
    return True
