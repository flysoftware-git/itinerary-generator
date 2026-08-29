"""
Itinerary Generator
==============================
Python CLI that converts a minimal YAML trip manifest into a single
self-contained HTML itinerary using multi-provider LLMs, xAI Grok semantic search, the NPS API,
and Wikimedia Commons.
"""

__version__ = "2.4.0"
# Tracks the frozen HTML template, separately from __version__ (see
# CHANGELOG.md). Not the template's FILENAME: only one template file has ever
# existed, templates/v2.5_template.html, and it is a fixed path rather than a
# version tag. This number is stamped into the published page.
__template_version__ = "2.6"
