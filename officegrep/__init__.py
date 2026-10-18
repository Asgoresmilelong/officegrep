"""officegrep —— 给 agent 用的本地文档全文检索。

    >>> from officegrep import Index
    >>> with Index("index.db") as idx:
    ...     idx.index_paths(["~/Documents"])
    ...     idx.search("弯矩")
"""

from __future__ import annotations

from .extract import SUPPORTED, Block, extract
from .index import Hit, Index

__version__ = "0.1.0"
__all__ = ["Index", "Hit", "Block", "extract", "SUPPORTED", "__version__"]
