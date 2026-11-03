# The texts for the week of the first post

Everything that goes out in the week of the first post, in its last version, in one place. Nothing
here is published by being in this directory; each piece goes out on its own, after it is approved.

| Piece | File |
|---|---|
| The English post | [docs/articles/algebraic-effects-in-python.md](../../docs/articles/algebraic-effects-in-python.md) |
| The Japanese translation for Zenn | [publications/zenn-use-cases-v0/doeff-main.md](../zenn-use-cases-v0/doeff-main.md) |
| PyCoder's Weekly suggestion | [pycoders.md](pycoders.md) |
| r/Python showcase post | [rpython.md](rpython.md) |
| X: first post and reply, English and Japanese | [x/en-1.txt](x/en-1.txt), [x/en-2.txt](x/en-2.txt), [x/ja-1.txt](x/ja-1.txt), [x/ja-2.txt](x/ja-2.txt) |
| X: the video on the first post | 139.4 s, 1280×720, 60 fps, with narration; the file is kept outside the repository and named by [x/video.sha256](x/video.sha256); its narration is [x/narration.json](x/narration.json) |
| X: the image on the reply | [x/reply-infographic.jpg](x/reply-infographic.jpg) |
| Show HN facts | [show-hn-facts.md](show-hn-facts.md) |

`check_no_ai_byline.py` fails if any of these pieces has a line that says an AI, an LLM or an
agent wrote it:

```bash
uv run --no-project python publications/launch-week-1/check_no_ai_byline.py
```
