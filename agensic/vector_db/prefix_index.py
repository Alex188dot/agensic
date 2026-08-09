"""Thread-safe radix-like prefix index for normalized shell commands."""

from __future__ import annotations

import threading


class _Node:
    __slots__ = ("children", "terminal")

    def __init__(self) -> None:
        self.children: dict[str, _Node] = {}
        self.terminal = False


class CommandPrefixIndex:
    """A compact character trie optimized for bounded prefix completions."""

    def __init__(self) -> None:
        self._root = _Node()
        self._lock = threading.RLock()
        self._size = 0

    def __len__(self) -> int:
        with self._lock:
            return self._size

    def add(self, command: str) -> bool:
        value = str(command or "")
        if not value:
            return False
        with self._lock:
            node = self._root
            for char in value:
                node = node.children.setdefault(char, _Node())
            if node.terminal:
                return False
            node.terminal = True
            self._size += 1
            return True

    def discard(self, command: str) -> bool:
        value = str(command or "")
        if not value:
            return False
        with self._lock:
            node = self._root
            path: list[tuple[_Node, str, _Node]] = []
            for char in value:
                child = node.children.get(char)
                if child is None:
                    return False
                path.append((node, char, child))
                node = child
            if not node.terminal:
                return False
            node.terminal = False
            self._size -= 1
            for parent, char, child in reversed(path):
                if child.terminal or child.children:
                    break
                parent.children.pop(char, None)
            return True

    def search(self, prefix: str, limit: int = 20) -> list[str]:
        value = str(prefix or "")
        row_limit = max(0, int(limit or 0))
        if not value or row_limit <= 0:
            return []
        with self._lock:
            node = self._root
            for char in value:
                node = node.children.get(char)
                if node is None:
                    return []

            results: list[str] = []
            stack: list[tuple[_Node, str]] = [(node, value)]
            while stack and len(results) < row_limit:
                current, text = stack.pop()
                if current.terminal:
                    results.append(text)
                    if len(results) >= row_limit:
                        break
                for char in sorted(current.children, reverse=True):
                    stack.append((current.children[char], text + char))
            return results

    def clear(self) -> None:
        with self._lock:
            self._root = _Node()
            self._size = 0
