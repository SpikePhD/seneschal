# Working on Seneschal

The earlier VtM-Storyteller project grew to 14,000 lines of engine around a model that was
not allowed to decide anything, and it refused ordinary play. Keep this project from repeating
that.

## Rules

- **The model decides, and the code remembers.** Add a tool that lets the Storyteller write
  something down. Do not add code that judges whether something is allowed.
- **Do not add lists of permitted actions, objects or effects.** If a behaviour is wrong, fix the
  instructions in `agents.py` first.
- **The player never sees a validation error.** A tool problem is returned to the model as a tool
  result. Only a failed model call reaches the player, and it changes nothing.
- **The code keeps only four guarantees:** real dice hidden until accepted, fixed secrets, a
  persistent notebook, and clocks that tick with time.
- **The prompt must stay bounded.** Anything new in `build_context` needs a cap, with the rest
  reachable through `recall`. `test_prompt_does_not_grow_with_the_chronicle` enforces this.
- **Stories and characters are data,** in `seneschal/content/`. Never write story-specific logic in
  the engine. `demo.py` is the only exception, because it is a test script.
- **`public_view` is the only path to the browser,** and it must not contain secrets, motives or
  private facts.
- **No runtime dependencies** without a strong reason. Python's standard library is enough so far.

## Checks

```
python -W error::RuntimeWarning -m unittest discover -s tests -q
node --check seneschal/web/app.js
```

Tests use a scripted model. They cannot show that the storytelling is good. For that, run
`python -m seneschal playtest` with a real model and read the transcripts.
