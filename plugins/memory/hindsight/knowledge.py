"""Knowledge-base tools: the bank's self-maintaining pages, organised in a folder tree.

Descriptions, parameter names and response shapes follow the Hindsight MCP server's
single-bank knowledge tools, so an agent sees the same contract through either route.
"""

from __future__ import annotations

import json
from typing import Any

# parent_id sentinel for "move to the root" on update (the client takes None for that).
_ROOT = "root"

_OPTIONAL_SETTINGS_HINT = (
    "Only pass optional settings the user explicitly asked for; anything omitted keeps "
    "the knowledge-page defaults."
)

# Mirrors the MCP server's MentalModelTriggerInput, minus tag_groups: the client's
# trigger builder does not convert nested tag groups yet. Fields carry no schema
# defaults on purpose - an omitted field must stay omitted, not be echoed back.
_TRIGGER = {
    "type": "object",
    "additionalProperties": False,
    "description": (
        "Optional refresh policy - when the page rebuilds and what it rebuilds from. "
        "Omitted fields keep the knowledge-page defaults: incremental (delta) rebuilds "
        "from consolidated observations after each consolidation, ignoring sibling pages. "
        + _OPTIONAL_SETTINGS_HINT
    ),
    "properties": {
        "mode": {"type": "string", "enum": ["full", "delta"], "description": (
            "Refresh mode. 'full' regenerates the content from scratch on each refresh; 'delta' "
            "makes surgical edits to the existing content, preserving unchanged sections "
            "byte-for-byte. Delta falls back to a full regeneration when there is no existing "
            "content or the source_query changed.")},
        "refresh_after_consolidation": {"type": "boolean", "description": (
            "Refresh automatically after observations are consolidated. Mutually exclusive "
            "with refresh_cron.")},
        "refresh_cron": {"type": "string", "description": (
            "UTC five-field cron schedule, e.g. '0 3 * * *' for daily at 03:00 UTC. A scheduled "
            "refresh runs only when the page is stale, so an unchanged scope costs no LLM call. "
            "Mutually exclusive with refresh_after_consolidation.")},
        "min_refresh_interval_seconds": {"type": "integer", "minimum": 0, "description": (
            "Minimum seconds between two AUTOMATIC refreshes. A trigger that arrives sooner is "
            "queued until the window expires, and further triggers fold into that one queued "
            "refresh. Explicit refreshes ignore it. 0 disables the floor; omit to use the "
            "bank/global setting.")},
        "fact_types": {"type": "array", "items": {"type": "string", "enum": ["world", "experience", "observation"]},
                       "description": "Fact types to retrieve during refresh."},
        "exclude_mental_models": {"type": "boolean", "description": (
            "Exclude ALL mental models from the refresh's reflect loop, so a page never "
            "reflects on its siblings.")},
        "exclude_mental_model_ids": {"type": "array", "items": {"type": "string"}, "description": (
            "Exclude specific mental models from the refresh's reflect loop, by ID.")},
        "tags_match": {"type": "string", "enum": ["any", "all", "any_strict", "all_strict", "exact"],
                       "description": (
            "How the page's tags select memories during refresh. Unset means 'all_strict' for a "
            "tagged page and 'any' for an untagged one.")},
        "include_chunks": {"type": "boolean", "description": (
            "Override whether the refresh's internal recall returns raw chunk text.")},
        "recall_max_tokens": {"type": "integer", "description": (
            "Override the token budget for facts from the refresh's internal recall.")},
        "recall_chunks_max_tokens": {"type": "integer", "description": (
            "Override the token budget for raw chunks from the refresh's internal recall.")},
        "reflect_search_observations_max_tokens": {"type": "integer", "minimum": 1, "description": (
            "Override the token budget for the refresh's search_observations calls.")},
        "reflect_search_observations_include_entities": {"type": "boolean", "description": (
            "Override whether search_observations attaches resolved entity names.")},
        "response_schema": {"type": "object", "description": (
            "JSON Schema for structured output, stored alongside the markdown content on each refresh.")},
        "keep_trace": {"type": "boolean", "description": (
            "Record how each refresh reached its result; only the latest refresh's trace is kept.")},
    },
}

KNOWLEDGE_LIST_SCHEMA = {
    "name": "hindsight_knowledge_list",
    "description": (
        "Browse the knowledge base as a nested tree of folders and pages. Start here to "
        "discover what the bank documents: each page is a living markdown document "
        "synthesized from the bank's memories, and folders group them. Use "
        "hindsight_knowledge_get to read a page's content, or hindsight_knowledge_search "
        "when you know what you are looking for. Pages report `is_stale`: false means the "
        "page is provably up to date; true means something was written since its last "
        "refresh, so it MAY be out of date."
    ),
    "parameters": {"type": "object", "properties": {}},
}

KNOWLEDGE_SEARCH_SCHEMA = {
    "name": "hindsight_knowledge_search",
    "description": (
        "Find knowledge pages by relevance (hybrid keyword + semantic search). Searches "
        "page names and content, returning ranked pages with a short snippet each. Read a "
        "hit in full with hindsight_knowledge_get. This searches the curated knowledge base "
        "only; use hindsight_recall to search raw memories."
    ),
    "parameters": {"type": "object", "required": ["query"], "properties": {
        "query": {"type": "string", "description": "What to search for"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 50,
                  "description": "Maximum pages to return (1-50, default: 10)"},
    }},
}

KNOWLEDGE_GET_SCHEMA = {
    "name": "hindsight_knowledge_get",
    "description": (
        "Read a knowledge page as a markdown document. Returns the page's YAML frontmatter "
        "(id, type, title, description, tags, timestamp) followed by its synthesized "
        "markdown body. Discover page ids with hindsight_knowledge_list or "
        "hindsight_knowledge_search."
    ),
    "parameters": {"type": "object", "required": ["page_id"], "properties": {
        "page_id": {"type": "string", "description": "The ID of the page to read (a `kp-...` node id)"},
    }},
}

KNOWLEDGE_CREATE_PAGE_SCHEMA = {
    "name": "hindsight_knowledge_create_page",
    "description": (
        "Create a knowledge page: a living document answering a question. The page's "
        "content is synthesized from the bank's memories by running source_query, "
        "asynchronously: read it with hindsight_knowledge_get once built. By default the "
        "page keeps itself current, rebuilding after each consolidation. A name and a "
        "question are all a page needs. " + _OPTIONAL_SETTINGS_HINT + "\n\nEXAMPLES:\n"
        '- name="Deployment Runbook", source_query="How is this service deployed and rolled back?"\n'
        '- name="Team Preferences", source_query="What tools and conventions does the team prefer?"'
    ),
    "parameters": {"type": "object", "required": ["name", "source_query"], "properties": {
        "name": {"type": "string", "description": "Page name (must be unique within its folder)"},
        "source_query": {"type": "string",
                         "description": "The question this page answers and rebuilds itself from"},
        "parent_id": {"type": "string", "description": (
            "Optional parent folder id (a `kf-...` node id). Omit to create at the top level.")},
        "tags": {"type": "array", "items": {"type": "string"},
                 "description": "Optional tags scoping which memories the page is built from"},
        "max_tokens": {"type": "integer", "minimum": 1,
                       "description": "Optional maximum tokens for the generated content"},
        "trigger": _TRIGGER,
    }},
}

KNOWLEDGE_CREATE_FOLDER_SCHEMA = {
    "name": "hindsight_knowledge_create_folder",
    "description": "Create a folder in the knowledge base. Folders group pages; they hold no content of their own.",
    "parameters": {"type": "object", "required": ["name"], "properties": {
        "name": {"type": "string", "description": "Folder name"},
        "parent_id": {"type": "string", "description": (
            "Optional parent folder id (a `kf-...` node id). Omit to create at the top level.")},
    }},
}

KNOWLEDGE_UPDATE_SCHEMA = {
    "name": "hindsight_knowledge_update",
    "description": (
        "Rename or move a folder/page, and/or update a page's options. Only the arguments "
        "you pass are changed; everything else keeps its current value. Changing "
        "source_query schedules an async refresh so the page rebuilds against the new "
        "question. " + _OPTIONAL_SETTINGS_HINT
    ),
    "parameters": {"type": "object", "required": ["node_id"], "properties": {
        "node_id": {"type": "string",
                    "description": "The ID of the folder (`kf-...`) or page (`kp-...`) to update"},
        "name": {"type": "string", "description": "New name for the node"},
        "parent_id": {"type": "string", "description": (
            f'Folder id to move the node into, or "{_ROOT}" to move it to the top level')},
        "source_query": {"type": "string", "description": "Pages only - the new question the page answers"},
        "tags": {"type": "array", "items": {"type": "string"},
                 "description": "Pages only - replacement tag list (pass [] to clear)"},
        "max_tokens": {"type": "integer", "minimum": 1,
                       "description": "Pages only - new maximum tokens for the generated content"},
        "trigger": {**_TRIGGER, "description": (
            "Pages only - refresh policy fields to change. This is a PATCH: fields you omit keep "
            "their current values, so putting a page on a cron schedule does not reset its "
            "delta mode or its observation-only scope. " + _OPTIONAL_SETTINGS_HINT)},
    }},
}

KNOWLEDGE_DELETE_SCHEMA = {
    "name": "hindsight_knowledge_delete",
    "description": (
        "Delete a knowledge-base folder or page and everything under it. Deleting a folder "
        "also deletes its whole subtree, and each deleted page takes its backing mental "
        "model with it. This cannot be undone."
    ),
    "parameters": {"type": "object", "required": ["node_id"], "properties": {
        "node_id": {"type": "string",
                    "description": "The ID of the folder (`kf-...`) or page (`kp-...`) to delete"},
    }},
}

KNOWLEDGE_SCHEMAS = [
    KNOWLEDGE_LIST_SCHEMA, KNOWLEDGE_SEARCH_SCHEMA, KNOWLEDGE_GET_SCHEMA,
    KNOWLEDGE_CREATE_PAGE_SCHEMA, KNOWLEDGE_CREATE_FOLDER_SCHEMA,
    KNOWLEDGE_UPDATE_SCHEMA, KNOWLEDGE_DELETE_SCHEMA,
]


def _api_error_detail(exc: Exception) -> str | None:
    """The server's own message for a failed API call (its ``detail`` field), instead
    of the client exception's text, which also dumps the HTTP response headers."""
    status, body = getattr(exc, "status", None), getattr(exc, "body", None)
    if status is None or not body:
        return None
    try:
        detail = json.loads(body).get("detail")
    except (ValueError, AttributeError):
        return f"HTTP {status}: {body}"
    if isinstance(detail, list):  # request validation errors
        detail = "; ".join(
            f"{'.'.join(str(p) for p in err.get('loc', []) if p != 'body')}: {err.get('msg')}"
            for err in detail if isinstance(err, dict)
        )
    return str(detail) if detail else f"HTTP {status}"


def _call(provider, method: str, **kwargs) -> Any:
    """Run one knowledge-base client coroutine against the provider's bank."""
    try:
        return provider._run_hindsight_operation(
            lambda client: getattr(client, method)(bank_id=provider._bank_id, **kwargs)
        )
    except Exception as exc:
        if (detail := _api_error_detail(exc)) is None:
            raise
        raise RuntimeError(detail) from exc


def _page_options(args: dict) -> dict[str, Any]:
    """The optional page settings the caller actually passed; the client sends only
    these, so the server keeps its own defaults (or the page's current values) for the rest."""
    return {k: args[k] for k in ("tags", "max_tokens", "trigger") if args.get(k) is not None}


def tool_list(provider, args: dict) -> dict:
    return _call(provider, "aget_knowledge_base_tree").to_dict()


def tool_search(provider, args: dict) -> dict:
    return _call(provider, "asearch_knowledge_base", q=args["query"],
                 limit=max(1, min(int(args.get("limit") or 10), 50))).to_dict()


def tool_get(provider, args: dict) -> dict:
    page = _call(provider, "aget_knowledge_page", page_id=args["page_id"]).to_dict()
    # The rendered markdown already carries the body under its frontmatter.
    page.pop("body", None)
    return page


def tool_create_page(provider, args: dict) -> dict:
    resp = _call(provider, "acreate_knowledge_page", name=args["name"],
                 source_query=args["source_query"], parent_id=args.get("parent_id") or None,
                 **_page_options(args))
    return {**resp.to_dict(), "status": "created",
            "message": f"Page '{args['name']}' created. Content is being generated asynchronously."}


def tool_create_folder(provider, args: dict) -> dict:
    return _call(provider, "acreate_knowledge_folder", name=args["name"],
                 parent_id=args.get("parent_id") or None).to_dict()


def tool_update(provider, args: dict) -> dict:
    kwargs: dict[str, Any] = {k: args[k] for k in ("name", "source_query") if args.get(k)}
    if parent_id := args.get("parent_id"):
        # The client sends parent_id only when passed; None means "move to the root".
        kwargs["parent_id"] = None if parent_id == _ROOT else parent_id
    return _call(provider, "aupdate_knowledge_node", node_id=args["node_id"],
                 **kwargs, **_page_options(args)).to_dict()


def tool_delete(provider, args: dict) -> dict:
    _call(provider, "adelete_knowledge_node", node_id=args["node_id"])
    return {"status": "deleted", "node_id": args["node_id"]}


# tool name -> (required args, handler, user-facing failure prefix)
KNOWLEDGE_TOOL_HANDLERS = {
    "hindsight_knowledge_list": ((), tool_list, "Failed to list the knowledge base"),
    "hindsight_knowledge_search": (("query",), tool_search, "Failed to search the knowledge base"),
    "hindsight_knowledge_get": (("page_id",), tool_get, "Failed to read knowledge page"),
    "hindsight_knowledge_create_page": (("name", "source_query"), tool_create_page,
                                        "Failed to create knowledge page"),
    "hindsight_knowledge_create_folder": (("name",), tool_create_folder, "Failed to create knowledge folder"),
    "hindsight_knowledge_update": (("node_id",), tool_update, "Failed to update knowledge node"),
    "hindsight_knowledge_delete": (("node_id",), tool_delete, "Failed to delete knowledge node"),
}
