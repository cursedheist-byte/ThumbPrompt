# """
# Game research module.

# Searches the web for information about a game and creates a compact
# research brief that can be passed to Gemma.
# """

# from duckduckgo_search import DDGS


# def research_game(game_name: str) -> str:
#     """
#     Research the actual game instead of interpreting the game name
#     literally.

#     Returns a compact research brief for the AI.
#     """

#     if not game_name:
#         return "No game name was provided."

#     queries = [
#         f"{game_name} game official gameplay",
#         f"{game_name} game gameplay screenshots",
#         f"{game_name} game mechanics visual style",
#     ]

#     results = []

#     try:
#         with DDGS() as ddgs:

#             for query in queries:

#                 search_results = ddgs.text(
#                     query,
#                     max_results=5,
#                 )

#                 for result in search_results or []:

#                     title = result.get("title", "")
#                     body = result.get("body", "")
#                     href = result.get("href", "")

#                     if title or body:
#                         results.append(
#                             {
#                                 "title": title,
#                                 "body": body,
#                                 "url": href,
#                             }
#                         )

#     except Exception as exc:
#         return (
#             f"Game research failed for '{game_name}'. "
#             f"Do NOT assume the literal meaning of the game name. "
#             f"Research error: {exc}"
#         )

#     if not results:
#         return (
#             f"No web research results were found for '{game_name}'. "
#             "Do not interpret the game name literally. "
#             "Use only information explicitly provided by the user."
#         )

#     # Remove duplicate results.
#     unique = []
#     seen = set()

#     for result in results:

#         key = (
#             result["title"].lower().strip(),
#             result["body"].lower().strip(),
#         )

#         if key in seen:
#             continue

#         seen.add(key)
#         unique.append(result)

#     # Keep the research brief reasonably small.
#     unique = unique[:10]

#     lines = [
#         f"GAME RESEARCH FOR: {game_name}",
#         "",
#         "IMPORTANT: This research is about the VIDEO GAME itself.",
#         "Do NOT interpret the game title literally if it contains "
#         "a common real-world word, animal, object, person, etc.",
#         "",
#     ]

#     for index, result in enumerate(unique, start=1):

#         lines.append(f"Source {index}:")
#         lines.append(f"Title: {result['title']}")
#         lines.append(f"Information: {result['body']}")

#         if result["url"]:
#             lines.append(f"Source URL: {result['url']}")

#         lines.append("")

#     return "\n".join(lines)