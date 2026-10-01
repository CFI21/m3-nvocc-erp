# CLX-054 Historical Preview

- Accepted baseline: `07d60f6ac8b39f7089a47f3ea16b9976232a3d80`
- Screen baseline: 196
- Purpose: side-by-side historical comparison with current CLX-077.
- Preview-only changes on this branch:
  1. Historical/read-only banner.
  2. Web API target points to isolated preview API.
  3. CORS/CSP points only to preview services.
  4. Mutating HTTP methods are blocked by `CLX054_PREVIEW_READ_ONLY=ON`.
- No production database URL or production credentials are used.
- This branch is not to be merged into main.
