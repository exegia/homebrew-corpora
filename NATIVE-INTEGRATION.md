# Experimental native TEI from the existing CLI

The CLI remains Python and uses the shared `admin.services.conversion` pipeline.
The default engine is unchanged. Explicitly select the native route with:

```sh
corpora convert input.tei --backend native-experimental --category document -o output.corpus
# A .xml filename needs --format tei as well.
```

This requires a corpora-py build containing the native TEI converter and the CFR
validator fix, the optional matching corpora-native wheel, and the verified
Context-Fabric 0.6.0rc1 runtime. The current Homebrew formula resolves released
packages from PyPI; installing the formula alone does not provide these source
changes or guarantee a native wheel. No release version is promised here.

Supported TEI uses `corpora-source/v1` and annotated-character projection.
Its source-preserving text and version-specific references are not byte-for-byte
interchangeable with the legacy Python word projection. The output path remains
the only stdout line; progress, diagnostics and fallback warnings use stderr.

Unsupported formats/categories and an absent optional wheel use explicit Python
capability fallback. Native semantic errors fail with exit 1 and diagnostics;
they do not retry through Python. An older corpora-py build that cannot select a
backend fails the explicit request with actionable guidance and exit 2.

Developer acceptance uses an isolated venv: install this checkout, the matching
corpora-py checkout, a locally built corpora-native wheel and
`context-fabric==0.6.0rc1`, then run `pytest tests/test_native_backend.py`.
The native tests skip in the ordinary public-package environment; run both
configurations. They verify a real native call, forbid silent Python parsing,
compare packaged source documents with direct native output, validate the
archive, and cover rejected semantics and explicit capability fallback.
