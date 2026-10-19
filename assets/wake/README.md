# The wake word model (audio/wake_local.py)

`encoder/decoder/joiner.int8.onnx` and `tokens.txt` are the int8 files of
**sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01** (k2-fsa, trained on
GigaSpeech XL, 10,000 hours of English), Apache License 2.0:
https://github.com/k2-fsa/sherpa-onnx/releases/tag/kws-models

`wake.txt` and `maybe.txt` are HeyTora's keywords, already split into the
model's BPE pieces (with its `bpe.model`, which is not shipped):

- `wake.txt` — "Hey / Hi / Okay / OK Tora": wake at once, threshold 0.25.
- `maybe.txt` — "Tora", "Torah", "Tara", "Dora", "Taura": the cloud check
  decides, threshold 0.10 with a 2.0 boost.

To change a phrase, tokenize it with the model's `bpe.model`
(`sentencepiece`, uppercase text) and keep the `:boost #threshold @NAME` form.
