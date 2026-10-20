"""统计本次作业的磁盘预算；可恢复 HPROF 索引缓存与下载量分别计量。"""

import os
from pathlib import Path
import re
import shutil
import stat
from contextlib import contextmanager

from .errors import AnalysisError


def unsafe():
    raise AnalysisError(
        "ANALYSIS_UNSAFE_PATH", "Analysis workspace contains an unsafe path"
    )


def attributes(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(info.st_mode) or getattr(path, "is_junction", lambda: False)():
        unsafe()
    return info


@contextmanager
def directory_handle(path, identity=None):
    """Linux 通过目录描述符访问工作区，避免路径检查后被替换成符号链接。"""
    info = attributes(path)
    if info is None or not stat.S_ISDIR(info.st_mode):
        unsafe()
    if identity is not None and (info.st_dev, info.st_ino) != identity:
        unsafe()
    if os.open in os.supports_dir_fd and os.scandir in os.supports_fd:
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError:
            unsafe()
        try:
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                unsafe()
            yield descriptor
        finally:
            os.close(descriptor)
    else:
        yield None


class WorkspaceBudget:
    def __init__(
        self, root, *, max_bytes, min_free_bytes, index_dir=None, index_digest=None
    ):
        self.root = Path(os.path.abspath(root))
        info = attributes(self.root)
        if info is None or not stat.S_ISDIR(info.st_mode):
            unsafe()
        self.identity = info.st_dev, info.st_ino
        self.max_bytes, self.min_free_bytes = max_bytes, min_free_bytes
        self.peak_bytes = 0
        self.index_dir, self.index_paths = None, []
        if index_dir is not None:
            self.index_dir = Path(os.path.abspath(index_dir))
            if self.index_dir == self.root / "indexes":
                # 作业内临时索引已包含在工作目录遍历中，不能重复计入缓存预算。
                self.index_dir = None
            elif (
                self.index_dir.parent != self.root.parent
                or self.index_dir.name != "indexes"
                or not re.fullmatch(r"[a-f0-9]{64}", index_digest or "")
            ):
                unsafe()
            if self.index_dir is not None:
                self.index_paths = [
                    self.index_dir / (f"hprof-{index_digest}.sqlite3" + suffix)
                    for suffix in ("", "-wal", "-shm")
                ]
        self.index_baseline = self._index_size()
        self.check()

    def _index_size(self):
        if self.index_dir is None:
            return 0
        info = attributes(self.index_dir)
        if info is None:
            return 0
        if not stat.S_ISDIR(info.st_mode):
            unsafe()
        size = 0
        with directory_handle(self.index_dir) as descriptor:
            for path in self.index_paths:
                try:
                    info = (
                        os.stat(path.name, dir_fd=descriptor, follow_symlinks=False)
                        if descriptor is not None
                        else attributes(path)
                    )
                except FileNotFoundError:
                    continue
                if info is not None:
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        unsafe()
                    size += info.st_size
        return size

    def _job_size(self):
        root_info = attributes(self.root)
        if (
            root_info is None
            or not stat.S_ISDIR(root_info.st_mode)
            or (root_info.st_dev, root_info.st_ino) != self.identity
        ):
            unsafe()
        size, entries = 0, 0

        def walk(directory, depth, descriptor=None):
            nonlocal size, entries
            if depth > 32:
                unsafe()
            # 每次 scandir 前先 lstat 当前目录，不跟随符号链接；
            # 此私有工作目录由本作业进程拥有。
            info = attributes(directory)
            if info is None:
                return
            if not stat.S_ISDIR(info.st_mode):
                unsafe()
            try:
                with os.scandir(
                    descriptor if descriptor is not None else directory
                ) as iterator:
                    for entry in iterator:
                        entries += 1
                        if entries > 4096:
                            raise AnalysisError(
                                "ANALYSIS_DISK_LIMIT",
                                "Analysis workspace exceeds file count limit",
                            )
                        path = directory / entry.name
                        try:
                            info = (
                                entry.stat(follow_symlinks=False)
                                if descriptor is not None
                                else attributes(path)
                            )
                        except FileNotFoundError:
                            continue
                        if info is None:
                            continue  # 外部工具可能已清理自己的临时文件，遍历时允许该文件消失。
                        if stat.S_ISDIR(info.st_mode):
                            if descriptor is None:
                                walk(path, depth + 1)
                            else:
                                try:
                                    child = os.open(
                                        entry.name,
                                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                        dir_fd=descriptor,
                                    )
                                except FileNotFoundError:
                                    continue
                                except OSError:
                                    unsafe()
                                try:
                                    walk(path, depth + 1, child)
                                finally:
                                    os.close(child)
                        elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                            size += info.st_size
                            if size > self.max_bytes:
                                raise AnalysisError(
                                    "ANALYSIS_DISK_LIMIT",
                                    "Analysis workspace exceeds its total byte budget",
                                )
                        else:
                            unsafe()
            except FileNotFoundError:
                return

        with directory_handle(self.root, self.identity) as descriptor:
            walk(self.root, 0, descriptor)
        return size

    def check(self, *, additional_bytes=0):
        if not isinstance(additional_bytes, int) or additional_bytes < 0:
            raise ValueError("Incoming byte count must be non-negative")
        job_bytes = self._job_size()
        index_growth = max(0, self._index_size() - self.index_baseline)
        total = job_bytes + index_growth
        self.peak_bytes = max(self.peak_bytes, total)
        if total + additional_bytes > self.max_bytes:
            raise AnalysisError(
                "ANALYSIS_DISK_LIMIT",
                "Analysis exceeds its total temporary disk budget",
            )
        paths = [self.root]
        if self.index_dir is not None and self.index_dir.exists():
            paths.append(self.index_dir)
        for path in paths:
            with directory_handle(
                path, self.identity if path == self.root else None
            ) as descriptor:
                if (
                    shutil.disk_usage(
                        descriptor if descriptor is not None else path
                    ).free
                    < self.min_free_bytes + additional_bytes
                ):
                    raise AnalysisError(
                        "ANALYSIS_DISK_SPACE",
                        "Analysis filesystem lacks its required free space reserve",
                        retryable=True,
                    )
        return {
            "job_disk_bytes": job_bytes,
            "index_growth_bytes": index_growth,
            "total_bytes": total,
        }
