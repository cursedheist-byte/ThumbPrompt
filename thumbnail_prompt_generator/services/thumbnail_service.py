"""
Orchestrates the thumbnail-generation workflow end to end:

  parse & validate wizard input
    -> (optionally) validate uploaded images
    -> call ai.prompt_engine for 3 concepts (1 Gemini call)
    -> persist input + concepts against a generation_id
    -> later: call ai.prompt_engine for the final prompt (1 Gemini call)

Routes in app.py should only ever call functions in this module (plus
usage_service for plan checks) — no Flask objects appear below this
layer, and no SQL/Gemini calls appear in app.py.
"""

from ai import prompt_engine
from database import database
from research.provider import get_research_provider
from services.upload_service import UploadValidationError, validate_and_read

REQUIRED_FIELDS = ["category", "topic", "title"]
VALID_CATEGORIES = {"gaming", "documentary", "vlog", "challenge", "entertainment", "tech", "educational"}


class ThumbnailServiceError(Exception):
    """User-facing validation error (bad input), distinct from AI/provider
    failures which bubble up as PromptEngineError."""


def _validate_input(form: dict) -> dict:
    category = (form.get("category") or "").strip().lower()
    topic = (form.get("topic") or "").strip()
    title = (form.get("title") or "").strip()

    if category not in VALID_CATEGORIES:
        raise ThumbnailServiceError("Please select a valid video category.")
    if not topic:
        raise ThumbnailServiceError(
            "Please tell us the game/topic this video is about."
        )
    if not title:
        raise ThumbnailServiceError("Please enter your video title.")
    if len(title) > 300:
        raise ThumbnailServiceError("Video title is too long (max 300 characters).")

    face_choice = None
    if category == "gaming":
        face_choice = (form.get("face_choice") or "without_face").strip().lower()
        if face_choice not in {"with_face", "without_face"}:
            raise ThumbnailServiceError("Invalid selection for face preference.")

    specific_elements_text = (form.get("specific_elements_text") or "").strip()[:1000]
    wants_text = (form.get("wants_text") or "").strip()[:200]

    return {
        "category": category,
        "topic": topic,
        "title": title,
        "face_choice": face_choice,
        "specific_elements_text": specific_elements_text,
        "wants_text": wants_text,
    }


def _collect_images(files: dict) -> tuple[list, dict]:
    """Validates any uploaded images, returns (image_parts_for_gemini,
    flags_dict) where flags_dict records which reference types were
    provided (used to steer prompt wording)."""
    images = []
    flags = {
        "has_face_image": False,
        "has_reference_thumbnail": False,
        "has_specific_element_image": False,
    }

    face_file = files.get("face_image")
    if face_file and face_file.filename:
        data = validate_and_read(face_file, "Face/reference image")
        face_file.stream.seek(0)
        images.append(
            {
                "bytes": data,
                "mime_type": face_file.mimetype or "image/jpeg",
                "label": "creator's face/identity reference (do not copy as full composition)",
            }
        )
        flags["has_face_image"] = True

    ref_thumb_file = files.get("reference_thumbnail")
    if ref_thumb_file and ref_thumb_file.filename:
        data = validate_and_read(ref_thumb_file, "Reference thumbnail")
        images.append(
            {
                "bytes": data,
                "mime_type": ref_thumb_file.mimetype or "image/jpeg",
                "label": "reference thumbnail for style analysis only (do not recreate)",
            }
        )
        flags["has_reference_thumbnail"] = True

    element_file = files.get("specific_element_image")
    if element_file and element_file.filename:
        data = validate_and_read(element_file, "Specific element image")
        images.append(
            {
                "bytes": data,
                "mime_type": element_file.mimetype or "image/jpeg",
                "label": "specific element the creator wants included",
            }
        )
        flags["has_specific_element_image"] = True

    return images, flags


def start_analysis(user_id: str, form: dict, files: dict) -> dict:
    """Validates input, calls Gemini for 3 concepts, persists the
    generation, and returns {generation_id, video_understanding, concepts}."""
    input_data = _validate_input(form)

    try:
        images, flags = _collect_images(files)
    except UploadValidationError as exc:
        raise ThumbnailServiceError(str(exc)) from exc

    input_data.update(flags)

    # Attach generalized strategic patterns from the pluggable research
    # layer. This never references a specific competitor thumbnail.
    research = get_research_provider()
    patterns = research.get_patterns(
        input_data["category"], input_data["topic"], input_data["title"]
    )
    input_data["strategy_patterns"] = patterns

    generation_id = database.create_generation(user_id, input_data)

    result = prompt_engine.generate_concepts(input_data, images)

    concepts = result.get("concepts", [])
    database.save_concepts(generation_id, concepts)

    return {
        "generation_id": generation_id,
        "video_understanding": result.get("video_understanding", ""),
        "concepts": concepts,
    }


def finalize_prompt(user_id: str, generation_id: str, concept_index: int, files: dict) -> dict:
    """Loads the stored generation, re-validates any freshly re-attached
    images (the browser resends the same files at finalize time so we
    don't have to persist raw image bytes server-side between requests),
    calls Gemini for the final master prompt, and persists it."""
    generation = database.get_generation(generation_id, user_id=user_id)
    if not generation:
        raise ThumbnailServiceError("Generation not found or expired. Please start over.")

    concepts = generation["concepts_json"] or []
    if concept_index is None or not (0 <= concept_index < len(concepts)):
        raise ThumbnailServiceError("Invalid concept selection.")

    selected_concept = concepts[concept_index]
    input_data = generation["input_json"]

    try:
        images, _flags = _collect_images(files)
    except UploadValidationError as exc:
        raise ThumbnailServiceError(str(exc)) from exc

    result = prompt_engine.generate_final_prompt(input_data, selected_concept, images)
    database.save_final_prompt(generation_id, result)

    return {
        "generation_id": generation_id,
        "final_image_prompt": result.get("final_image_prompt", ""),
        "structured_breakdown": result.get("structured_breakdown", {}),
        "selected_concept": selected_concept,
    }
