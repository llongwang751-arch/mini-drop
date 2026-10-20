# Python Saber performance integration

This optional integration enrolls a separately provided, user-owned Python
Saber repository. Business source is not distributed here. Only
`internal/rag/fts5_index.py` and its original `tests/test_fts5_index.py` are
exported, together with the fixed harness and SHA manifest. This is a component
benchmark, not deployment of the complete Saber application, Agent or RAG stack.

Run from the Drop repository root and supply the source location explicitly:

```sh
python integrations/saber-python/package.py --help
python integrations/saber-python/package.py --source /path/to/AGI-saber-python/final --output .local/integrations/saber-python/context
```

On Windows, use the corresponding quoted absolute source path. The output must
be new or empty, outside the source tree, and contain no symlink path components.
No default local desktop path is read. The original source is not modified;
environment files, business databases and unrelated application files are not
exported. Review `manifest.json`, then build `Dockerfile` with the generated
directory as its context and an explicit new image tag. The archive and source
receipt are written beside the output directory.

An administrator must separately register this immutable source revision and
the reviewed workload/sandbox images with `ops/enroll_code_project.py`. Merely
cloning Drop does not register external source or recreate production's private
source snapshot. See [Saber integration](../../docs/SABER_INTEGRATION.md) for the
enrollment and demonstration boundaries. Generated patches are tested in an
isolated copy; this integration never publishes changes to the original Saber
repository.
