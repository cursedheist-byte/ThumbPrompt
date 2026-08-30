"""
Thumbnail Prompt Engine

Builds the two prompts sent to Gemini:
1. Thumbnail concept generation
2. Final master image prompt generation

This module handles prompt construction and response validation.
"""

import json

from ai.gemini_client import (
    GeminiClient,
    GeminiClientError,
    GeminiNotConfiguredError,
)
from ai.schemas import (
    CONCEPTS_RESPONSE_SCHEMA,
    FINAL_PROMPT_RESPONSE_SCHEMA,
    SchemaValidationError,
    validate_concepts_response,
    validate_final_prompt_response,
)
from config import Config
from prompts.thumbnail_system_prompt import THUMBNAIL_STRATEGIST_SYSTEM_PROMPT


class PromptEngineError(Exception):
    """Wraps AI-provider or validation failures."""


# ============================================================
# USER INPUT DESCRIPTION
# ============================================================

def _describe_inputs(input_data: dict) -> str:
    """Convert wizard data into a compact AI brief."""

    category = input_data.get("category") or "Not specified"
    topic = input_data.get("topic") or "Not specified"
    title = input_data.get("title") or "Not specified"

    lines = [
        f"Video category: {category}",
        f"Game/topic: {topic}",
        f"Video title: {title}",
    ]

    # -------------------------
    # FACE
    # -------------------------

    if category == "gaming":

        face_choice = input_data.get("face_choice")

        if face_choice == "with_face":
            lines.append(
                "Creator should appear in the thumbnail. "
                "An uploaded face image is provided as the direct "
                "facial identity reference."
            )
        else:
            lines.append(
                "Creator should NOT appear in the thumbnail."
            )

    # -------------------------
    # SPECIFIC USER REQUEST
    # -------------------------

    specific = input_data.get("specific_elements_text")

    if specific:
        lines.append(
            f"Specific things requested by the creator: {specific}"
        )

    # -------------------------
    # UPLOADED REFERENCES
    # -------------------------

    if input_data.get("has_face_image"):
        lines.append(
            "UPLOADED FACE IMAGE AVAILABLE: "
            "Use the uploaded face image as the direct identity "
            "reference for the creator."
        )

    if input_data.get("has_reference_thumbnail"):
        lines.append(
            "UPLOADED REFERENCE THUMBNAIL AVAILABLE: "
            "Use it ONLY to understand high-level visual strategy "
            "such as composition, framing, lighting, hierarchy, "
            "scale and mood. Do not recreate or copy it."
        )

    if input_data.get("has_specific_element_image"):
        lines.append(
            "UPLOADED SPECIFIC-ELEMENT IMAGE AVAILABLE: "
            "Use it as the reference for the requested gameplay "
            "element, object or character."
        )

    return "\n".join(lines)


def _compact_schema(schema: dict) -> str:
    """Serialize JSON schema compactly to reduce token usage."""

    return json.dumps(
        schema,
        separators=(",", ":"),
    )


# ============================================================
# CONCEPT PROMPT
# ============================================================

def build_concepts_prompt(input_data: dict) -> str:
    """Build the first Gemini request."""

    brief = _describe_inputs(input_data)
    schema = _compact_schema(CONCEPTS_RESPONSE_SCHEMA)

    gaming_rules = ""

    if input_data.get("category") == "gaming":

        game_name = input_data.get("topic") or "the specified game"

        gaming_rules = f"""
==================================================
GAME IDENTITY — CRITICAL
==================================================

This is a thumbnail for the VIDEO GAME:

"{game_name}"

Treat "{game_name}" as the actual game title.

Do NOT interpret individual words in the game title literally.

For example:
- If the game name contains "chameleon", do NOT assume a real
  chameleon.
- If the game name contains an animal, object or common word,
  do NOT automatically create the real-world version of that word.

The concepts must visually belong to the actual "{game_name}"
video game.

Use game-specific characters, players, environments, objects,
gameplay situations and visual language when appropriate.

CHARACTER RULE:

When a concept requires a player/character from the game, prefer
wording such as:

"a player from the {game_name} video game"

or:

"a character from the {game_name} video game"

rather than inventing a completely unrelated generic character.

Do NOT invent a giant robot, futuristic machine, cyberpunk object,
spaceship, hologram, sci-fi structure or other spectacular element
unless it is actually relevant to "{game_name}" or the user's
video.

Do NOT add something just because it makes the thumbnail look
"epic".

The video's actual content has priority over spectacle.

Do NOT automatically force:
8K, 16K, ultra realistic, hyper realistic, cinematic photorealism,
HDR, lens flares, excessive depth of field, artificial AI gloss,
or other generic AI-image aesthetics.

Preserve the recognizable native visual style of the actual game.

==================================================
"""

    return f"""
You are an expert YouTube thumbnail strategist.

Your job is to understand the actual video and design a thumbnail
that communicates its idea immediately.

USER BRIEF:
{brief}

{gaming_rules}

TASK:

Create EXACTLY 3 ORIGINAL thumbnail concepts.

Each concept must have a genuinely different strategic approach
that fits THIS video.

Possible approaches include:
- drama
- scale
- contrast
- curiosity
- mystery
- reaction
- transformation
- danger
- challenge
- visual storytelling

Do NOT force an approach that does not fit the video.

For every concept provide:

- concept_name
- core_visual_idea
- composition
- subject_placement
- camera
- lighting
- background
- emotional_hook
- important_objects
- why_it_could_work

Keep descriptions specific and useful.

REFERENCE THUMBNAIL RULE:

If a reference thumbnail is attached, analyze its high-level
visual strategy only.

Do NOT recreate it.
Do NOT copy its exact composition.
Do NOT copy unique artwork, characters or logos.

FACE RULE:

If a face image is attached, it is the direct identity reference
for the creator. Do not invent a different face.

Return ONLY valid JSON matching this schema:

{schema}
""".strip()


# ============================================================
# FINAL PROMPT
# ============================================================

def build_final_prompt_prompt(
    input_data: dict,
    selected_concept: dict,
) -> str:
    """Build the second Gemini request."""

    brief = _describe_inputs(input_data)

    concept_json = json.dumps(
        selected_concept,
        separators=(",", ":"),
    )

    schema = _compact_schema(
        FINAL_PROMPT_RESPONSE_SCHEMA
    )

    # -------------------------
    # TEXT
    # -------------------------

    wants_text = input_data.get("wants_text")

    if wants_text:
        text_instruction = (
            f'The user explicitly wants this text in the thumbnail: '
            f'"{wants_text}". Keep it short, bold and readable.'
        )
    else:
        text_instruction = (
            "The user did not request text. Do NOT add text, letters "
            "or numbers to the thumbnail."
        )

    # -------------------------
    # FACE
    # -------------------------
    #
    # NOTE: `face_choice` is expected to carry the wizard's face-usage
    # mode (e.g. "just a reactor" -> face shown as-is / "put in game"
    # -> face integrated into the game world). The check below matches
    # on substrings so it keeps working regardless of the exact enum
    # string used elsewhere in the app. If your actual face_choice
    # values differ, adjust the two substring checks below only.

    if input_data.get("has_face_image"):

        face_choice_raw = (input_data.get("face_choice") or "").lower()
        put_in_game = "game" in face_choice_raw

        if put_in_game:
            face_instruction = """
==================================================
UPLOADED FACE IMAGE — HARD RULE (PUT IN GAME)
==================================================

An uploaded face image is available and the creator has chosen to
be integrated into the game world/character style.

The final_image_prompt must explicitly refer to the "uploaded face
image" or "uploaded face reference" as the creator's identity.

The creator may be styled as a player/character from the game
(pose, outfit, framing, lighting, integration into the scene), but
the FACE ITSELF must remain unchanged.

Use wording such as:

"a player/character from the [GAME NAME] video game with the
creator's face taken directly from the uploaded face reference,
preserving the uploaded face exactly"

Do NOT invent a replacement face.
Do NOT redesign or reinterpret the facial features.
Do NOT write a detailed textual description of the creator's face.
"""
        else:
            face_instruction = """
==================================================
UPLOADED FACE IMAGE — HARD RULE
==================================================

An uploaded face image is available.

The final_image_prompt must explicitly refer to the "uploaded face
image" or "uploaded face reference" as the creator's identity.

Do NOT invent or describe the creator's facial features in detail.
Do NOT generate a different face.

The face must remain unchanged from the uploaded reference.
"""

    else:
        face_instruction = ""

    # -------------------------
    # GAMING
    # -------------------------

    if input_data.get("category") == "gaming":

        game_name = input_data.get("topic") or "the specified game"

        gaming_instruction = f"""
==================================================
GAME RESEARCH — DO THIS FIRST
==================================================

Before generating this image, research the actual video game
"{game_name}" to understand its real visual identity, gameplay,
characters, environments, maps, objects and art direction.

Specifically research:

- the game's official visual style
- actual player/character designs
- actual environments and maps
- gameplay mechanics
- recognizable gameplay objects
- materials and textures
- colors
- overall art direction
- screenshots or gameplay footage, when available

Use this research as the foundation for the thumbnail.

If web/search capability is available, research the actual game
first. If browsing is unavailable, use the game name and any
provided reference images as context, and do NOT invent unsupported
game details.

==================================================
CRITICAL GAME-NAME RULE
==================================================

GAME:
"{game_name}"

This is a thumbnail for the ACTUAL "{game_name}" VIDEO GAME.

Do NOT interpret the game title literally.

If the game name contains a common real-world word, animal,
object, person or place, do NOT automatically create that
real-world thing.

Example:

Game: "Mecha Chameleon"

BAD:
Assuming "chameleon" means a real chameleon, and generating
"a giant realistic chameleon".

GOOD:
Research the actual "Mecha Chameleon" VIDEO GAME first, then use
its actual characters, environments and visual identity.

The game name refers to the VIDEO GAME, not the literal meaning of
individual words in the title.

The final prompt must explicitly establish that the environment,
characters, players and important objects belong to the
"{game_name}" video game.

==================================================
GAME CHARACTER RULE
==================================================

Do NOT invent a detailed physical description of a game's
character when the actual game character can be identified
through research.

Prefer wording such as:

"a player from the {game_name} video game"

or:

"a character from the {game_name} video game"

instead of:

"a blue robotic creature with..."

or:

"a futuristic mechanical chameleon..."

unless those details are actually confirmed by the game's
research.

The game's researched visual identity takes priority over the AI's
imagination.

Do NOT write detailed invented descriptions such as:
- "a blue robotic chameleon"
- "a futuristic humanoid robot"
- "a colorful mechanical creature"
- "a small reptilian character with..."
- or any other AI-invented character description.

ONLY describe the character's pose, action, expression, scale or
position when needed for the composition.

Example:

BAD:
"a giant blue robotic chameleon character standing over..."

GOOD:
"a giant player from the {game_name} video game standing over..."

BAD:
"a tiny colorful mechanical reptile looking upward..."

GOOD:
"a tiny player from the {game_name} video game looking upward..."

==================================================
GAME ENVIRONMENT
==================================================

The environment must look like it belongs to the actual
researched "{game_name}" game.

Preserve its recognizable native:
- art direction
- environment
- materials
- architecture
- characters
- gameplay elements
- visual language

Do NOT automatically transform the game into generic
photorealistic AI artwork.

==================================================
NO GENERIC AI GARBAGE
==================================================

Do NOT automatically add:

- giant robots
- futuristic machines
- cyberpunk elements
- spaceships
- holograms
- glowing circuitry
- futuristic cities
- mechanical creatures
- sci-fi technology
- 8K
- 16K
- ultra realistic
- hyper realistic
- generic cinematic photorealism
- insanely detailed
- masterpiece
- award winning
- excessive HDR
- lens flares
- artificial AI gloss

unless they are actually relevant to the researched game or the
user's video topic.

Never add a robot or futuristic element merely because it looks
"epic" or cinematic.

Every major visual element must be relevant to the user's video.

RELEVANCE > SPECTACLE.
"""

    else:
        gaming_instruction = ""

    # -------------------------
    # REFERENCE THUMBNAIL
    # -------------------------

    if input_data.get("has_reference_thumbnail"):

        reference_instruction = """
==================================================
REFERENCE THUMBNAIL — INSPIRATION ONLY
==================================================

An uploaded reference thumbnail is available.

Use it only to understand high-level visual principles:

- composition
- framing
- subject placement
- scale
- lighting
- color relationships
- visual hierarchy
- mood
- negative space

Create an ORIGINAL thumbnail.

Do NOT recreate the reference.
Do NOT copy its exact layout.
Do NOT copy unique artwork.
Do NOT copy distinctive characters.
Do NOT copy logos.
Do NOT reproduce its exact visual identity.
"""

    else:

        reference_instruction = """
No reference thumbnail was provided.
Do not imply that one exists.
"""

    # -------------------------
    # SPECIFIC ELEMENT IMAGE
    # -------------------------

    if input_data.get("has_specific_element_image"):

        element_instruction = """
==================================================
UPLOADED SPECIFIC ELEMENT
==================================================

An uploaded reference image of a specific gameplay element,
object or character is available.

Refer to it as the "uploaded reference image".

Use it to preserve the relevant identity and appearance of that
specific element.

Do not replace it with an unrelated generic object.
"""

    else:

        element_instruction = ""

    # -------------------------
    # 16:9
    # -------------------------

    aspect_ratio_instruction = """
==================================================
ASPECT RATIO — MANDATORY
==================================================

The thumbnail MUST be designed specifically as a 16:9 YouTube
thumbnail.

The final image-generation prompt MUST explicitly say:

"16:9 YouTube thumbnail composition"

Keep all important subjects safely inside the frame.

Do not design a square or portrait composition.
"""

    # -------------------------
    # RELEVANCE
    # -------------------------

    relevance_instruction = """
==================================================
RELEVANCE — HARD RULE
==================================================

The thumbnail should visually communicate the ACTUAL VIDEO.

Do not add major objects, creatures, characters, vehicles,
robots, futuristic technology, explosions or special effects
just to make the image look more impressive.

Before adding any major visual element, ask:

"Is this relevant to the user's video?"

If not, do not add it.

A simple accurate thumbnail is better than an impressive
but irrelevant AI-generated scene.
"""

    return f"""
You are an expert YouTube thumbnail art director and
image-generation prompt engineer.

USER BRIEF:
{brief}

SELECTED THUMBNAIL CONCEPT:
{concept_json}

{text_instruction}

{aspect_ratio_instruction}

{face_instruction}

{gaming_instruction}

{reference_instruction}

{element_instruction}

{relevance_instruction}

==================================================
FINAL TASK
==================================================

Turn the selected concept into ONE production-ready image-generation
prompt.

The goal is not generic "AI art".

The goal is a highly effective YouTube thumbnail that:

1. Communicates the video idea immediately.
2. Has one dominant focal point.
3. Has strong visual hierarchy.
4. Is readable at small thumbnail size.
5. Uses a deliberate 16:9 composition.
6. Looks appropriate to the actual game when this is a gaming video.
7. Uses the uploaded face image correctly when provided.
8. Uses uploaded gameplay/object references correctly.
9. Uses reference thumbnails only as inspiration.
10. Avoids irrelevant visual elements.
11. Looks intentionally designed rather than randomly generated.

If this is a gaming video, the final_image_prompt MUST START with
the "GAME RESEARCH — DO THIS FIRST" instruction described above,
telling the image generator to research the actual game before
designing the image.

The structured breakdown must cover:

- visual_style
- composition
- camera_angle
- camera_distance
- subject_placement
- character_appearance
- facial_expression
- body_pose
- important_objects
- object_scale
- foreground
- background
- lighting
- color_relationships
- depth_and_atmosphere
- visual_hierarchy
- storytelling
- emotional_impact
- readability_at_small_size
- reference_image_instructions
- preserve_from_reference
- do_not_copy_from_reference
- negative_instructions
- text_instructions

IMPORTANT:

If a face reference exists, the final_image_prompt MUST explicitly
refer to the "uploaded face image" or "uploaded face reference".

Do NOT replace the face reference with a long textual description
of the creator's facial features.

If this is a gaming video, the final_image_prompt MUST explicitly
mention the actual game name:

"{input_data.get("topic") or "the specified game"}"

and make clear that the visual elements belong to that game, based
on research into the actual game rather than invented details.

If a player/character is needed, prefer:

"a player/character from the {input_data.get("topic") or "specified game"} video game"

instead of inventing a generic unrelated character.

The final_image_prompt must be natural and directly copy-pasteable
into an image-generation model.

Do not write the final prompt like a technical checklist.

Return ONLY valid JSON matching this schema:

{schema}
""".strip()


# ============================================================
# GENERATE CONCEPTS
# ============================================================

def generate_concepts(
    input_data: dict,
    images: list,
) -> dict:
    """Generate three thumbnail concepts."""

    client = GeminiClient()

    prompt_text = build_concepts_prompt(
        input_data
    )

    try:

        result = client.generate_json(
            system_instruction=THUMBNAIL_STRATEGIST_SYSTEM_PROMPT,
            prompt_text=prompt_text,
            images=images,
            temperature=Config.GEMINI_TEMPERATURE_CONCEPTS,
        )

        validate_concepts_response(result)

        return result

    except GeminiNotConfiguredError:
        raise

    except (
        GeminiClientError,
        SchemaValidationError,
    ) as exc:

        raise PromptEngineError(
            str(exc)
        ) from exc


# ============================================================
# GENERATE FINAL PROMPT
# ============================================================

def generate_final_prompt(
    input_data: dict,
    selected_concept: dict,
    images: list,
) -> dict:
    """Generate the final production-ready image prompt."""

    client = GeminiClient()

    prompt_text = build_final_prompt_prompt(
        input_data,
        selected_concept,
    )

    try:

        result = client.generate_json(
            system_instruction=THUMBNAIL_STRATEGIST_SYSTEM_PROMPT,
            prompt_text=prompt_text,
            images=images,
            temperature=Config.GEMINI_TEMPERATURE_FINAL,
        )

        validate_final_prompt_response(result)

        return result

    except GeminiNotConfiguredError:
        raise

    except (
        GeminiClientError,
        SchemaValidationError,
    ) as exc:

        raise PromptEngineError(
            str(exc)
        ) from exc