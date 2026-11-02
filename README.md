# Seneschal

A storyteller for one player, starting with Vampire: The Masquerade.

The model is the Storyteller. It knows every secret, judges what you try, invents what the
story needs, and voices every character. The code is its notebook and its dice. That split is
the whole design:

| The model decides | The code guarantees |
| --- | --- |
| What is plausible, and what it takes | Dice are really rolled, and hidden from the model until you accept them |
| What exists: new places, people and objects | Whatever is written down is still there next turn |
| What each character says and does | Secrets set at the start can't be rewritten |
| Whether the story should turn | Time passes and clocks tick, even if nobody mentions them |
| What failure costs | The prompt stays the same size, however long the chronicle runs |

There is deliberately no list of allowed actions. If you say "I should have a knife", the
Storyteller says yes, asks a question, makes you roll, or tells you no in the fiction. It is
never an error.

## Run it

Python 3.11 or newer. There is nothing to install.

```
cp .env.example .env     # add a Mistral or OpenAI key
python -m seneschal      # then open http://127.0.0.1:8765/
```

Without a key, the game runs on a canned script. That script is useful for trying the interface
and has no judgement, so it says nothing about how real play feels.

Choose models under **Settings**. Use your strongest model for the Storyteller.

## Judge it by reading transcripts

Passing tests can't tell you whether this is a good storyteller. This command plays scripted
scenarios against your configured model, using your API credits, and writes what happened to
`playtests/`:

```
python -m seneschal playtest                 # every scenario
python -m seneschal playtest --only knife xray
```

The scenarios are `knife`, `xray`, `open_box`, `pushy_player` and `drift`. Each transcript ends
with the notebook: the story so far, the drift log, the secrets revealed and every tool used.

## How a turn works

1. **The prompt is rebuilt from the notebook:** the premise, secrets, who and what is here, the
   clocks, the story so far, and the last few lines word for word.
2. **The Storyteller uses tools:** it makes things real, moves them, passes time and calls for
   rolls. If a tool call can't be carried out, the problem goes back to the model to correct.
3. **You decide on any roll:** you can accept the dice or spend Willpower to reroll. Only then
   does the Storyteller learn the result and narrate it.
4. **A turn is all or nothing:** if the model call fails, the game is unchanged and your words
   stay in the box.
5. **The Director works between scenes:** it moves NPCs offscreen, rewrites the story so far and
   checks for drift. It runs in the background every few turns or after enough in-game time,
   and it never writes to you. Your next action waits for it if it is still working.

### Long games

Older conversation drops out of the prompt, but not out of the notebook. The Director keeps a
summary, and the Storyteller can use the `recall` tool to search everything that has ever been
said or established.

### Drift

The story is allowed to leave its premise when your choices earn that. Departures are recorded in
a drift log with their reasons, and abandoned threads keep moving: the deadline still passes.
To read the log, open **Optional details** under the composer. It contains spoilers.

## Add to it

Stories and characters are plain TOML, so no code is needed:

- `seneschal/content/characters/`: reusable people, each with a personality, motive, plan,
  voice and what they know.
- `seneschal/content/stories/`: a premise, a player sheet, places, cast, secrets and clocks.

## Layout

| File | Purpose |
| --- | --- |
| `seneschal/world.py` | The notebook: state, clocks, recall, saving, what the browser may see |
| `seneschal/tools.py` | What the Storyteller and Director can do |
| `seneschal/agents.py` | Their instructions, the prompt builder, and the turn loop |
| `seneschal/dice.py` | V5 dice |
| `seneschal/llm.py` | Model client and settings |
| `seneschal/server.py` | Local web server |
| `seneschal/demo.py` | The canned script used without a key and in tests |
| `seneschal/playtest.py` | Transcript playtests |
| `seneschal/web/` | The interface |

Run the tests with `python -m unittest discover -s tests`. They use a scripted model, so they
check the machinery and not the storytelling.

## Current limits

- Combat is run by the Storyteller through ordinary rolls. There is no dedicated system for
  rounds, opposed pools or weapon damage yet.
- Only Mistral and OpenAI are configured. Any API that uses the same chat-completions format can
  be added to `PROVIDERS` in `llm.py`.
- The Storyteller reads secrets so that it can judge against them. Nothing but its instructions
  stops it from hinting at one.
