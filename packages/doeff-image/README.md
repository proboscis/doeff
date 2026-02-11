# doeff-image

Provider-agnostic image effects for `doeff`.

This package defines shared domain effects and result types:

- `ImageGenerate` for text-to-image requests
- `ImageEdit` for image-to-image / transformation requests
- `ImageResult` as a unified result payload (`list[PIL.Image.Image]`)

Provider packages (for example `doeff-seedream`, `doeff-gemini`) can implement
model-routed handlers that consume these effects and delegate unsupported models.
