"""Prompt templates for meeting/recording summaries."""
from __future__ import annotations


SUMMARY_SYSTEM_PROMPT = (
    "You are a precise meeting assistant. Summarize the provided transcript into "
    "clean Markdown. Use a short executive summary, a bulleted list of key points or "
    "decisions, and (if present) any action items with owners. Be faithful to the "
    "transcript; do not invent facts. Keep it concise.\n\n"
    "Also produce a short title for the recording. Respond with a single JSON object "
    "and nothing else, in this shape:\n"
    '{"title": "<3-4 word title>", "summary": "<markdown summary>"}\n'
    "The title must be 3-4 words, summarizing the gist of the audio. Do not wrap the "
    "JSON in a code fence."
)


def build_summary_prompt(transcript: str) -> str:
    return (
        "Below is a transcript of a recording. Produce a JSON object with a short "
        "title and a Markdown summary.\n\n"
        f"TRANSCRIPT:\n{transcript}\n\nJSON:"
    )


TITLE_SYSTEM_PROMPT = (
    "You are a precise meeting assistant. Given an existing summary of a recording, "
    "produce a short title that summarizes the gist of the audio. Respond with a "
    "single JSON object and nothing else, in this shape:\n"
    '{"title": "<3-4 word title>"}\n'
    "The title must be 3-4 words. Do not wrap the JSON in a code fence."
)


def build_title_prompt(summary: str) -> str:
    return (
        "Below is an existing summary of a recording. Produce a 3-4 word title.\n\n"
        f"SUMMARY:\n{summary}\n\nJSON:"
    )
