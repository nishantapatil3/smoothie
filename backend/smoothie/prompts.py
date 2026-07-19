"""Prompt template for meeting/recording summaries."""
from __future__ import annotations


SUMMARY_SYSTEM_PROMPT = (
    "You are a precise meeting assistant. Summarize the provided transcript into "
    "clean Markdown. Use a short executive summary, a bulleted list of key points or "
    "decisions, and (if present) any action items with owners. Be faithful to the "
    "transcript; do not invent facts. Keep it concise."
)


def build_summary_prompt(transcript: str) -> str:
    return (
        "Below is a transcript of a recording. Produce a Markdown summary.\n\n"
        f"TRANSCRIPT:\n{transcript}\n\nSUMMARY:"
    )
