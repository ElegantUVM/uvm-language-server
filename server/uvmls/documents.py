"""LSP documents use CR/LF line endings, including when applying incremental changes."""

import re

from pygls.workspace import Workspace as PyglsWorkspace
from pygls.workspace.text_document import TextDocument as PyglsTextDocument

LINE_RE = re.compile(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+$")


class TextDocument(PyglsTextDocument):
    @property
    def source(self) -> str:
        if self._source is None and self.path is not None:
            with open(self.path, encoding="utf-8", newline="") as f:
                return f.read()
        return self._source or ""

    @property
    def lines(self) -> tuple[str, ...]:
        # str.splitlines also splits page breaks and Unicode separators, unlike LSP and slang.
        return tuple(LINE_RE.findall(self.source))


class Workspace(PyglsWorkspace):
    def _create_text_document(self, doc_uri, source=None, version=None, language_id=None):
        return TextDocument(doc_uri, source=source, version=version, language_id=language_id,
                            sync_kind=self._sync_kind, position_codec=self._position_codec)
