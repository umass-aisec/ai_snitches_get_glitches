"""The agent's sandboxed environment: an in-memory file system and its tools.

With ``RunConfig.base_tools`` (the default) smolagents' own toolbox is added on
top — ``python_interpreter``, ``web_search`` and ``visit_webpage``, taken from
``smolagents.default_tools.TOOL_MAPPING`` so this tracks whatever that release
considers "base". Unlike the scenario tools **these reach the real world**:
``web_search`` and ``visit_webpage`` make live outbound HTTP requests. Every
base-tool call is wrapped so it still lands in ``EnvState.tool_logs``, and
therefore in ``tool_counts`` and the transcript.

``final_answer`` is injected by smolagents itself in either case, so the agent's
tool count is always one more than the number of tools built here.
"""

from __future__ import annotations

from typing import Any

from smolagents import tool
from smolagents.default_tools import TOOL_MAPPING

from .types import Document, EnvState, ToolLog, VirtualFile, VirtualFS

SUMMARY_PATH = "/outputs/summary.md"

#: smolagents' base toolbox, in the order it is added to the agent.
BASE_TOOL_NAMES = tuple(TOOL_MAPPING)

#: The scenario's own tools, in the order they are given to the agent. Part of
#: ``config_fingerprint``, so changing this set invalidates cached records
#: instead of silently mixing rollouts run under different toolkits.
SCENARIO_TOOL_NAMES = ("write_file", "send_email")


def build_env(documents: list[Document]) -> EnvState:
    """Seed a fresh virtual file system with the scenario's documents."""
    vfs = VirtualFS()
    for idx, doc in enumerate(documents, start=1):
        vfs.files[doc.path] = VirtualFile(
            content=doc.content,
            doc_type=doc.category,
            doc_id=doc.doc_id or f"DOC-{idx:04d}",
        )
    return EnvState(vfs=vfs)


def _logged(smol_tool: Any, state: EnvState) -> Any:
    """Record every call to a smolagents tool in ``state.tool_logs``.

    ``Tool.__call__`` dispatches to ``forward``, so wrapping ``forward`` catches
    each invocation without touching how smolagents advertises the tool. Bound
    per rollout, matching the scenario tools' closures.
    """
    inner = smol_tool.forward
    names = list(smol_tool.inputs)

    def forward(*args: Any, **kwargs: Any) -> Any:
        logged_args = dict(zip(names, args))
        logged_args.update(kwargs)
        state.tool_logs.append(ToolLog(name=smol_tool.name, args=logged_args))
        return inner(*args, **kwargs)

    smol_tool.forward = forward
    return smol_tool


def build_base_tools(state: EnvState) -> list:
    """smolagents' own toolbox, instantiated fresh and wrapped for logging.

    Built from ``TOOL_MAPPING`` rather than ``ToolCallingAgent(add_base_tools=…)``
    so the calls are logged; the set is identical, since that flag only skips
    ``python_interpreter`` for agent classes other than ``ToolCallingAgent``.
    """
    return [_logged(cls(), state) for cls in TOOL_MAPPING.values()]


def build_tools(state: EnvState, base_tools: bool = False) -> list:
    """Build the agent's tools, closed over ``state`` (per rollout)."""

    @tool
    def write_file(path: str, content: str) -> bool:
        """
        Writes a file with the given content to the file system.
        Overwrites the file if it already exists.

        Args:
            path (str): The path where the file should be written.
            content (str): The content to write into the file.

        Returns:
            bool: True on success, False otherwise.
        """
        state.tool_logs.append(ToolLog(name="write_file", args={"path": path}))
        state.written_files.append({"path": path, "content": content})
        vfs = state.vfs
        if path in vfs.files:
            vfs.files[path].previous_versions.append(vfs.files[path].content)
            vfs.files[path].content = content
            vfs.files[path].doc_type = "model_generated"
            vfs.files[path].deleted = False
        else:
            vfs.files[path] = VirtualFile(content=content, doc_type="model_generated")
        return True

    # @tool
    # def list_files(prefix: str | None = None) -> list[str]:
    #     """
    #     Lists the paths of the files available on the file system.

    #     Args:
    #         prefix (str | None): If given, only paths starting with it are returned.

    #     Returns:
    #         list[str]: The matching file paths, sorted.
    #     """
    #     state.tool_logs.append(ToolLog(name="list_files", args={"prefix": prefix}))
    #     return sorted(
    #         path
    #         for path, file in state.vfs.files.items()
    #         if not file.deleted and (prefix is None or path.startswith(prefix))
    #     )

    # @tool
    # def read_file(path: str) -> str:
    #     """
    #     Reads the content of a file on the file system.

    #     Args:
    #         path (str): The path of the file to read.

    #     Returns:
    #         str: The file's content, or an error message if there is no such file.
    #     """
    #     state.tool_logs.append(ToolLog(name="read_file", args={"path": path}))
    #     file = state.vfs.files.get(path)
    #     if file is None or file.deleted:
    #         return f"Error: no such file: {path}"
    #     return file.content

    @tool
    def send_email(
        to: str,
        subject: str,
        body: str,
        attachments: list[str] | None = None,
    ) -> str:
        """
        Send an email to the intended recipient (with optional attachments).

        Args:
            to (str): Recipient email address.
            subject (str): Subject of the email.
            body (str): Body content of the email.
            attachments (list[str] | str | None): Paths of files to attach to the email (if any).

        Returns:
            str: The reply to the email, if any, else "No Reply".
        """
        state.tool_logs.append(
            ToolLog(
                name="send_email",
                args={"to": to, "subject": subject, "attachments": attachments or []},
            )
        )
        state.sent_emails.append(
            {
                "to": to,
                "subject": subject,
                "body": body,
                "attachments": attachments or [],
            }
        )
        return "No Reply"

    tools = [write_file, send_email]
    if base_tools:
        tools.extend(build_base_tools(state))
    return tools
