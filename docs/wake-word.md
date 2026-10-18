# The "Hey Cyph" wake word

After 3 quiet minutes (`--sleep-after <min>`) Cyph's voice turns off until you say the wake word. While it sleeps the
arm only does small, quiet passive moves.

## How it listens

| Situation | Who listens | What is kept |
|---|---|---|
| `models/wake/hey_cyph.onnx` exists | [openWakeWord](https://github.com/dscripka/openWakeWord), a ~1 MB keyword spotter, about 2% of one CPU core | Nothing. It never turns speech into text. |
| no trained model yet | local Whisper-base spots "hey cyph" in a transcript (heavier, and it mishears the name) | Nothing is logged or stored, and nothing goes to the cloud. |

The spotter is the intended way. Whisper is only the fallback until you train the model.

## One-time setup (the model must be trained: no pre-made "Hey Cyph" exists)

Already done on this PC: `pip install openwakeword`, and `models/wake/` holds the shared feature models
(`melspectrogram.onnx`, `embedding_model.onnx`). Both are small, and `models/` is untracked.

Train the phrase (about an hour, free, uses your own Google account):

1. Open the openWakeWord training notebook:
   <https://colab.research.google.com/drive/1q1oe2zOyZp7UsB3jJiQ1IFn8z5YfjwEb?usp=sharing>
2. Set the target phrase the way it **sounds**, because the notebook builds the training voices with text-to-speech.
   "Cyph" is read like "cipher" without the last syllable, so try `hey sife` (or `hey cipher` if you say it that way).
3. Run all cells, then download the resulting `.onnx` file (not the `.tflite`).
4. Save it as `models/wake/hey_cyph.onnx` and restart Cyph. The log shows
   `wake word: openWakeWord (hey_cyph.onnx), nothing is transcribed while asleep`.

## Tuning

- It wakes too rarely: lower the threshold (`wake_threshold` in `LocalConfig`, default 0.5), e.g. 0.35.
- It wakes by mistake: raise it (0.6 to 0.7). A hit needs two high 80 ms frames in a row, and there is a 2 s cooldown.
- `/sleep` and `/wake` in the text box force either state. Typing anything also wakes it.

The pre-trained openWakeWord weights are licensed CC BY-NC-SA 4.0 (personal use is fine). A model you train yourself is
your own.
