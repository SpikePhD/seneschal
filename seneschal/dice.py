"""V5 dice. The model chooses what is rolled and how hard it is; only this file rolls."""
import random

ATTRIBUTES = ("strength", "dexterity", "stamina", "charisma", "manipulation", "composure",
              "intelligence", "wits", "resolve")
SKILLS = ("athletics", "brawl", "craft", "drive", "firearms", "larceny", "melee", "stealth", "survival",
          "animal ken", "etiquette", "insight", "intimidation", "leadership", "performance", "persuasion",
          "streetwise", "subterfuge", "academics", "awareness", "finance", "investigation", "medicine",
          "occult", "politics", "science", "technology")
MAX_REROLL = 3


def score(regular, hunger, difficulty):
    """Return the V5 result for a set of dice."""
    dice = list(regular) + list(hunger)
    tens = dice.count(10)
    successes = sum(die >= 6 for die in dice) + 2 * (tens // 2)
    passed = successes >= difficulty
    if passed and tens >= 2:
        outcome = "messy_critical" if 10 in hunger else "critical"
    elif passed:
        outcome = "success"
    else:
        outcome = "bestial_failure" if 1 in hunger else "failure"
    return {"successes": successes, "margin": successes - difficulty, "passed": passed, "outcome": outcome}


def roll(pool, hunger, difficulty, rng=random):
    """Hunger dice replace regular dice, up to the size of the pool."""
    pool = max(1, pool)
    hungry = min(pool, max(0, hunger))
    regular_dice = [rng.randint(1, 10) for _ in range(pool - hungry)]
    hunger_dice = [rng.randint(1, 10) for _ in range(hungry)]
    return {"regular": regular_dice, "hunger": hunger_dice, "difficulty": difficulty,
            **score(regular_dice, hunger_dice, difficulty)}


def reroll(result, indices, rng=random):
    """A Willpower reroll: up to three regular dice, once. Hunger dice are never rerolled."""
    chosen = sorted(set(indices))
    if not chosen or len(chosen) > MAX_REROLL:
        raise ValueError(f"Choose between 1 and {MAX_REROLL} regular dice.")
    if any(type(i) is not int or not 0 <= i < len(result["regular"]) for i in chosen):
        raise ValueError("Only regular dice can be rerolled.")
    regular_dice = list(result["regular"])
    for index in chosen:
        regular_dice[index] = rng.randint(1, 10)
    return {**result, "regular": regular_dice,
            **score(regular_dice, result["hunger"], result["difficulty"])}


def rouse(rng=random):
    """A Rouse check: 6+ passes, otherwise Hunger rises."""
    die = rng.randint(1, 10)
    return {"die": die, "passed": die >= 6}
