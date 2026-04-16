import re


def format_response_for_discord(response: str) -> str:
    if not response:
        return ""

    text = response.replace("\r\n", "\n").strip()
    text = text.replace("\t", "  ")
    text = re.sub(r"\n{3,}", "\n\n", text)

    # If the model forgot to close a fenced code block, close it here to avoid
    # breaking message formatting in Discord.
    if text.count("```") % 2 == 1:
        text += "\n```"

    return text


def _chunk_text(text: str, max_length: int):
    chunks = []
    current = ""
    in_code_block = False

    for line in text.split("\n"):
        addition = line if not current else f"\n{line}"
        if len(current) + len(addition) <= max_length:
            current += addition
            if "```" in line:
                in_code_block = (line.count("```") % 2 == 1) ^ in_code_block
            continue

        if current:
            if in_code_block and not current.rstrip().endswith("```"):
                current += "\n```"
            chunks.append(current.strip())

        current = line
        if in_code_block and not current.lstrip().startswith("```"):
            current = "```\n" + current

        if "```" in line:
            in_code_block = line.count("```") % 2 == 1

    if current:
        if in_code_block and not current.rstrip().endswith("```"):
            current += "\n```"
        chunks.append(current.strip())

    return [chunk for chunk in chunks if chunk]


def split_response(response, max_length=1999):
    formatted = format_response_for_discord(response)
    if len(formatted) <= max_length:
        return [formatted]
    return _chunk_text(formatted, max_length)