# Vendored FlameGraph scripts

This directory vendors the following scripts from Brendan Gregg's FlameGraph project:

- `flamegraph.pl`
- `stackcollapse-perf.pl`

They are included so the analyzer can run `perf script -> stackcollapse-perf.pl -> flamegraph.pl`
without downloading tools during a demo. The scripts retain their original copyright headers
and are distributed under the CDDL 1.0 license. See `cddl1.txt` in this directory.
