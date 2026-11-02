/* The player's view of the chronicle. The server owns the game; this only draws it and sends
   what the player does. Everything from the server is inserted as text, never as HTML. */
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var STORAGE_KEY = "seneschal.selected-game";
  var app = {
    view: null, games: [], stories: [], settings: null, draft: null,
    busy: false, pending: null, recipient: "npc", selected: new Set(), poll: null, returnFocus: null
  };

  // Helpers ------------------------------------------------------------------------------

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }
  function empty(node) { while (node.firstChild) node.removeChild(node.firstChild); return node; }
  function setText(id, value) { $(id).textContent = value === undefined || value === null ? "" : String(value); }
  function title(value) { value = String(value); return value.charAt(0).toUpperCase() + value.slice(1); }
  function remember(id) { try { window.localStorage.setItem(STORAGE_KEY, id); } catch (ignore) {} }
  function remembered() { try { return window.localStorage.getItem(STORAGE_KEY) || ""; } catch (ignore) { return ""; } }

  function toast(message, isError) {
    var item = el("div", "toast" + (isError ? " error" : ""), message);
    item.setAttribute("role", isError ? "alert" : "status");
    $("toast-region").appendChild(item);
    window.setTimeout(function () { item.remove(); }, 6000);
  }

  function announce(message) {
    var node = $("announcer");
    node.textContent = "";
    window.setTimeout(function () { node.textContent = message; }, 20);
  }

  function setConnection(state, label) {
    $("connection-state").setAttribute("data-state", state);
    setText("connection-label", label);
  }

  async function api(path, method, body) {
    var options = { method: method || "GET", headers: { Accept: "application/json" }, cache: "no-store" };
    if (body !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    var response;
    try {
      response = await fetch(path, options);
    } catch (cause) {
      setConnection("error", "Server not reachable");
      throw new Error("The local server is not reachable. Your words are still here.");
    }
    setConnection("online", "Running locally");
    var payload = {};
    try { payload = await response.json(); } catch (ignore) {}
    if (!response.ok) {
      var failure = new Error(payload.message || "That didn't work. Your words are still here.");
      failure.code = payload.code;
      throw failure;
    }
    return payload;
  }

  function setBusy(value, message) {
    app.busy = value;
    $("busy-bar").hidden = !value;
    if (message) setText("busy-copy", message);
    renderControls();
    if (app.view) renderRoll(app.view);
  }

  function mode(view) {
    if (view && view.conflict) return "combat";
    if (view && view.dialogue) return "dialogue";
    return "storyteller";
  }

  // Rendering ----------------------------------------------------------------------------

  function pips(id, filled, total, label) {
    var node = empty($(id));
    for (var i = 0; i < total; i += 1) node.appendChild(el("span", "pip" + (i < filled ? " filled" : "")));
    node.setAttribute("aria-label", label + " " + filled + " of " + total);
  }

  function stats(id, values) {
    var node = empty($(id));
    Object.keys(values).forEach(function (key) {
      var row = el("div");
      row.appendChild(el("dt", "", title(key)));
      row.appendChild(el("dd", "", values[key]));
      node.appendChild(row);
    });
  }

  function listInto(id, values, renderer) {
    var node = empty($(id));
    values.forEach(function (value) { node.appendChild(el("li", "", renderer ? renderer(value) : value)); });
    return node;
  }

  function optional(detailsId, listId, values) {
    $(detailsId).hidden = !values.length;
    listInto(listId, values);
  }

  function renderCharacter(view) {
    var c = view.character;
    setText("character-heading", c.name);
    setText("character-clan", c.clan + (c.concept ? " · " + c.concept : ""));
    var health = c.health.max - c.health.superficial - c.health.aggravated;
    var will = c.willpower.max - c.willpower.superficial - c.willpower.aggravated;
    setText("character-status", view.ending ? "Ended" : health <= 0 ? "Impaired" : view.conflict ? "In conflict" : "Awake");
    setText("hunger-value", c.hunger + " / 5");
    setText("health-value", health + " / " + c.health.max);
    setText("willpower-value", will + " / " + c.willpower.max);
    pips("hunger-pips", c.hunger, 5, "Hunger");
    pips("health-pips", health, c.health.max, "Health");
    pips("willpower-pips", will, c.willpower.max, "Willpower");
    stats("attributes-list", c.attributes);
    stats("skills-list", c.skills);
    setText("condition-copy", c.conditions.length ? c.conditions.join("; ") : "No active condition reported.");
    if (c.equipment.length) {
      listInto("inventory-list", c.equipment, function (item) { return item.name; });
      Array.prototype.forEach.call($("inventory-list").children, function (row, index) { row.title = c.equipment[index].description; });
    } else {
      empty($("inventory-list")).appendChild(el("li", "empty-note", "Nothing recorded."));
    }
    optional("disciplines-details", "disciplines-list", Object.keys(c.disciplines).map(function (key) { return key + " " + c.disciplines[key]; }));
    optional("backgrounds-details", "backgrounds-list", c.backgrounds);
    optional("havens-details", "havens-list", c.havens);
    var journal = empty($("journal-list"));
    if (!view.journal.length) journal.appendChild(el("p", "empty-note", "Your notes will appear here."));
    view.journal.forEach(function (entry) { journal.appendChild(el("p", "journal-entry", entry)); });
  }

  function renderHeader(view) {
    setText("chronicle-kicker", view.kicker);
    setText("chronicle-title", view.name);
    setText("chronicle-briefing", view.briefing);
    setText("story-time", view.time);
    setText("story-location", view.place);
    document.title = view.name + " · Seneschal";
  }

  function renderScene(view) {
    setText("scene-heading", view.scene.title);
    setText("scene-copy", view.scene.description);
    var people = empty($("visible-people"));
    if (!view.people.length) people.appendChild(el("li", "empty-note", "No one else is visible."));
    view.people.forEach(function (person) {
      var row = el("li");
      row.title = person.description;
      var meta = el("span", "person-meta");
      meta.appendChild(el("span", "person-name", person.name));
      meta.appendChild(el("span", "person-status", view.dialogue && view.dialogue.target === person.id ? "In conversation" : "Present"));
      row.appendChild(meta);
      var talk = el("button", "person-action", "Talk");
      talk.type = "button";
      talk.dataset.action = "talk";
      talk.addEventListener("click", function () { startDialogue(person.id); });
      row.appendChild(talk);
      people.appendChild(row);
    });
    var exits = empty($("exits-list"));
    if (!view.exits.length) exits.appendChild(el("span", "empty-note", "No exits reported."));
    view.exits.forEach(function (exit) {
      var button = el("button", "exit-button");
      button.type = "button";
      button.dataset.action = "exit";
      button.appendChild(el("span", "exit-label", exit.name));
      button.appendChild(el("span", "exit-arrow", "Go ↗"));
      button.addEventListener("click", function () { send("I go to " + exit.name + ".", "storyteller"); });
      exits.appendChild(button);
    });
    $("fiction-visible-objects-section").hidden = !view.around.length;
    listInto("fiction-visible-objects", view.around, function (item) { return item.name; });
    $("complication-notice").hidden = !view.complication;
    setText("complication-copy", view.complication || "");
    setText("premise-copy", view.premise);
    var pressures = empty($("pressure-list"));
    pressures.hidden = !view.clocks.length;
    view.clocks.forEach(function (clock) {
      var row = el("div", "pressure");
      row.appendChild(el("span", "pressure-name", clock.name));
      var track = el("div", "pips");
      for (var i = 0; i < clock.segments; i += 1) track.appendChild(el("span", "pip" + (i < clock.filled ? " filled" : "")));
      track.setAttribute("role", "img");
      track.setAttribute("aria-label", clock.name + ": " + clock.filled + " of " + clock.segments);
      row.appendChild(track);
      pressures.appendChild(row);
    });
    $("thread-list").hidden = !view.threads.length;
    listInto("thread-list", view.threads);
    var facts = empty($("facts-list"));
    if (!view.facts.length) facts.appendChild(el("p", "empty-note", "No facts have been recorded."));
    view.facts.forEach(function (fact) {
      var row = el("dl", "fact-entry");
      row.appendChild(el("dt", "", fact.time));
      row.appendChild(el("dd", "", fact.text));
      facts.appendChild(row);
    });
    setText("local-mode", title(mode(view)));
    setText("local-record", view.turn);
  }

  function renderMessage(message) {
    if (message.type === "event") {
      var event = el("div", "timeline-event");
      event.appendChild(el("span", "timeline-event-time", message.time));
      var body = el("div", "timeline-event-body");
      body.appendChild(el("span", "timeline-event-label", message.label || "Event"));
      body.appendChild(el("span", "timeline-event-text", message.text));
      event.appendChild(body);
      return event;
    }
    var kinds = { player_message: "is-player", npc_message: "is-npc is-dialogue", storyteller_message: "is-storyteller" };
    var channel = message.channel && message.channel !== "storyteller" ? " mode-" + message.channel : "";
    var row = el("article", "timeline-message " + (kinds[message.type] || "is-storyteller") + channel);
    var inner = el("div", "timeline-message-body");
    var header = el("div", "timeline-message-header");
    var role = message.type === "player_message" ? "You" : message.type === "npc_message" ? (message.speaker || "Someone") : "Storyteller";
    header.appendChild(el("span", "timeline-message-role", role.toUpperCase()));
    if (message.time) header.appendChild(el("span", "timeline-message-time", message.time));
    inner.appendChild(header);
    inner.appendChild(el("p", "timeline-message-text", message.text));
    row.appendChild(inner);
    return row;
  }

  function renderTimeline(view) {
    var node = $("transcript");
    var atBottom = node.scrollHeight - node.scrollTop - node.clientHeight < 80;
    empty(node);
    view.timeline.forEach(function (message) { node.appendChild(renderMessage(message)); });
    if (app.pending) {
      var waiting = renderMessage({ type: "player_message", text: app.pending.text, channel: app.pending.channel });
      waiting.classList.add("is-pending");
      node.appendChild(waiting);
    }
    if (atBottom || app.pending) node.scrollTop = node.scrollHeight;
    setText("transcript-live", view.ending ? "Closed" : "Local record");
  }

  function renderPanels(view) {
    var ask = view.clarification;
    $("clarification-panel").hidden = !ask;
    if (ask) {
      setText("clarification-heading", "Tell me how");
      setText("clarification-copy", ask.question);
      setText("clarification-original", ask.original);
      $("proposal-original-block").hidden = !ask.original;
    }
    var plan = view.plan;
    $("plan-panel").hidden = !plan;
    if (plan) {
      setText("plan-goal", plan.goal);
      var steps = empty($("plan-steps"));
      plan.steps.forEach(function (step) {
        var row = el("li", "plan-step");
        row.dataset.status = step.status;
        row.appendChild(el("span", "plan-step-label", step.label + (step.optional ? " (if needed)" : "")));
        var result = step.status === "open" ? "" : " · " + title(step.status);
        row.appendChild(el("span", "plan-step-check", title(step.attribute) + " + " + title(step.skill) + " · Difficulty " + step.difficulty + result));
        row.appendChild(el("span", "plan-step-stakes", "If it fails: " + step.stakes));
        steps.appendChild(row);
      });
    }
    var conflict = view.conflict;
    $("combat-banner").hidden = !conflict;
    if (conflict) {
      setText("combat-heading", conflict.label);
      setText("combat-round", "Round " + conflict.round);
    }
    $("ending-card").hidden = !view.ending;
    if (view.ending) {
      setText("ending-summary", view.ending.summary);
      setText("ending-outcome", view.ending.outcome);
    }
    var working = view.director_status === "working";
    $("narration-status").hidden = !working;
  }

  function renderRoll(view) {
    var roll = view.pending_roll;
    $("roll-panel").hidden = !roll;
    if (!roll) { app.selected.clear(); return; }
    var c = view.character;
    var spent = c.willpower.superficial + c.willpower.aggravated >= c.willpower.max;
    var canReroll = !roll.rerolled && !spent && !app.busy && roll.regular.length > 0;
    setText("roll-attempt", roll.attempt);
    setText("roll-explainer", title(roll.attribute) + " + " + title(roll.skill) + ", Difficulty " + roll.difficulty + ". If it fails: " + roll.stakes);
    setText("roll-badge", roll.successes + " vs " + roll.difficulty + " · " + (roll.passed ? "Passing" : "Failing"));
    setText("regular-legal-copy", canReroll ? "Choose up to three" : "Cannot be rerolled");
    var regular = empty($("regular-dice"));
    roll.regular.forEach(function (value, index) {
      var die = el("button", "die" + (canReroll ? " legal" : " prohibited") + (app.selected.has(index) ? " selected" : ""), value);
      die.type = "button";
      die.disabled = !canReroll;
      die.setAttribute("aria-pressed", app.selected.has(index) ? "true" : "false");
      die.setAttribute("aria-label", "Regular die showing " + value);
      die.addEventListener("click", function () {
        if (app.selected.has(index)) app.selected.delete(index);
        else if (app.selected.size < 3) app.selected.add(index);
        renderRoll(app.view);
      });
      regular.appendChild(die);
    });
    var hunger = empty($("hunger-dice"));
    roll.hunger.forEach(function (value) {
      var die = el("span", "die hunger", value);
      die.setAttribute("aria-label", "Hunger die showing " + value);
      hunger.appendChild(die);
    });
    if (!roll.hunger.length) hunger.appendChild(el("span", "muted", "None"));
    $("reroll-button").disabled = !canReroll || app.selected.size === 0;
    $("resolve-button").disabled = app.busy;
    setText("roll-legal-note", roll.rerolled ? "You have already rerolled this." : spent ? "You have no Willpower left to spend."
      : roll.regular.length ? "Rerolling costs one superficial Willpower damage." : "This roll has only Hunger dice, so Willpower can't help.");
  }

  function renderControls() {
    var view = app.view;
    var modeName = mode(view);
    document.body.setAttribute("data-interaction-mode", modeName);
    document.body.setAttribute("data-dialogue-recipient", modeName === "dialogue" ? app.recipient : "");
    var dialogue = modeName === "dialogue";
    $("dialogue-controls").hidden = !dialogue;
    $("recipient-npc").setAttribute("aria-pressed", String(app.recipient === "npc"));
    $("recipient-storyteller").setAttribute("aria-pressed", String(app.recipient === "storyteller"));
    var toStoryteller = dialogue && app.recipient === "storyteller";
    setText("composer-mode-label", modeName === "combat" ? "Combat mode" : dialogue ? (toStoryteller ? "Aside to the Storyteller" : "Dialogue with " + view.dialogue.name) : "Storyteller mode");
    setText("composer-label-copy", modeName === "combat" ? "Say what you do this round." : toStoryteller ? "Only the Storyteller hears this." : dialogue ? "Your exact words are spoken aloud." : "Say what you do in your own words. The scene will respond.");
    $("composer-input").placeholder = dialogue && !toStoryteller ? "What do you say?" : toStoryteller ? "Ask the Storyteller…" : "Say what you do…";
    var blocked = !view || app.busy || !!view.ending;
    var rolling = !!(view && view.pending_roll);
    $("composer-input").disabled = blocked || rolling;
    $("composer-submit").disabled = blocked || rolling;
    setText("composer-help", !view ? "Begin a chronicle to play." : view.ending ? "This chronicle has ended." : rolling ? "Decide on the dice first." : "Try anything. The Storyteller decides what it takes and what it costs.");
    document.querySelectorAll('[data-action="exit"], [data-action="talk"], #continue-button').forEach(function (button) { button.disabled = blocked || rolling; });
  }

  function render(view) {
    app.view = view;
    renderCharacter(view);
    renderHeader(view);
    renderScene(view);
    renderTimeline(view);
    renderPanels(view);
    renderRoll(view);
    renderControls();
    if ($("debug-panel").open) loadNotebook();
    watchDirector(view);
  }

  // The director works in the background; its results appear when it finishes.
  function watchDirector(view) {
    window.clearTimeout(app.poll);
    if (view.director_status !== "working") return;
    app.poll = window.setTimeout(async function () {
      if (app.busy || !app.view || app.view.id !== view.id) return;
      try {
        var fresh = await api("/api/games/" + view.id);
        if (!app.busy && app.view && app.view.id === view.id) render(fresh);
      } catch (ignore) { watchDirector(view); }
    }, 2000);
  }

  // Playing ------------------------------------------------------------------------------

  async function send(text, channel, target, recipient) {
    if (!app.view || app.busy) return false;
    var view = app.view;
    app.pending = { text: text, channel: channel === "dialogue" && recipient === "npc" ? "dialogue" : (view.conflict ? "combat" : "storyteller") };
    setBusy(true, "The Storyteller is thinking…");
    renderTimeline(view);
    try {
      var fresh = await api("/api/games/" + view.id + "/act", "POST", { text: text, channel: channel, target: target, recipient: recipient });
      app.pending = null;
      app.busy = false;
      $("busy-bar").hidden = true;
      render(fresh);
      announce("The Storyteller has replied.");
      return true;
    } catch (error) {
      app.pending = null;
      toast(error.message, true);
      renderTimeline(view);
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function submitComposer(event) {
    event.preventDefault();
    var input = $("composer-input");
    var text = input.value.trim();
    if (!text || !app.view) return;
    var view = app.view;
    var sent;
    if (view.dialogue && !view.conflict) sent = await send(text, "dialogue", view.dialogue.target, app.recipient);
    else sent = await send(text, "storyteller");
    if (sent) { input.value = ""; updateCount(); input.focus(); }
  }

  function updateCount() { setText("char-count", $("composer-input").value.length + " / 2000"); }

  async function startDialogue(target) {
    if (!app.view) return;
    try {
      app.recipient = "npc";
      render(await api("/api/games/" + app.view.id + "/dialogue", "POST", { target: target }));
      $("composer-input").focus();
    } catch (error) { toast(error.message, true); }
  }

  async function exitDialogue() {
    try { render(await api("/api/games/" + app.view.id + "/dialogue", "POST", { target: null })); }
    catch (error) { toast(error.message, true); }
  }

  async function rollAction(action) {
    if (!app.view || app.busy) return;
    var view = app.view;
    setBusy(true, action === "accept" ? "The Storyteller is reading the dice…" : "Rerolling…");
    try {
      var fresh = await api("/api/games/" + view.id + "/roll", "POST", { action: action, dice: Array.from(app.selected) });
      app.selected.clear();
      app.busy = false;
      render(fresh);
    } catch (error) {
      toast(error.message, true);
    } finally {
      setBusy(false);
    }
  }

  async function loadNotebook() {
    if (!app.view) return;
    var list = empty($("debug-detail-list"));
    try {
      var book = await api("/api/games/" + app.view.id + "/notebook");
      var rows = [
        ["Story so far", book.summary || "(the director hasn't written one yet)"],
        ["Director's note", book.pressure || "(none)"],
        ["Director", book.director.note || book.director.status],
        ["Drift log", book.drift.map(function (d) { return d.change + " (why: " + d.why + ")"; }).join("\n") || "(no departures so far)"],
        ["NPC plans", Object.keys(book.people).map(function (k) { var p = book.people[k]; return k + " at " + p.location + ": " + p.plan; }).join("\n")],
        ["Clocks", Object.keys(book.clocks).map(function (k) { var c = book.clocks[k]; return c.name + ": " + c.filled + "/" + c.segments; }).join("\n")],
        ["Secrets", Object.keys(book.secrets).map(function (k) { var s = book.secrets[k]; return k + (s.revealed ? " [known]" : " [hidden]") + ": " + s.truth; }).join("\n")],
        ["Recent tools", book.log.slice(-15).map(function (e) { return e.by + ": " + e.tool + " → " + e.result; }).join("\n") || "(none)"]
      ];
      empty(list);
      rows.forEach(function (row) {
        var wrap = el("div");
        wrap.appendChild(el("dt", "", row[0]));
        wrap.appendChild(el("dd", "", row[1]));
        list.appendChild(wrap);
      });
    } catch (error) { toast(error.message, true); }
  }

  // Chronicles drawer ----------------------------------------------------------------------

  function toggleDialog(panelId, backdropId, buttonId, open, focusId) {
    var panel = $(panelId);
    panel.hidden = false;
    panel.classList.toggle("is-open", open);
    panel.setAttribute("aria-hidden", String(!open));
    panel.inert = !open;
    $(backdropId).hidden = !open;
    $(buttonId).setAttribute("aria-expanded", String(open));
    if (open) {
      app.returnFocus = document.activeElement;
      $(focusId).focus();
    } else {
      window.setTimeout(function () { if (!panel.classList.contains("is-open")) panel.hidden = true; }, 200);
      if (app.returnFocus && app.returnFocus.focus) app.returnFocus.focus();
    }
  }

  function renderDrawer() {
    var select = $("new-chronicle-story");
    var chosen = select.value;
    empty(select);
    app.stories.forEach(function (story) {
      var option = el("option", "", story.title);
      option.value = story.id;
      select.appendChild(option);
    });
    if (chosen) select.value = chosen;
    syncPitch(false);
    setText("saved-count", app.games.length);
    var list = empty($("saved-games"));
    if (!app.games.length) list.appendChild(el("p", "empty-note", "No saved nights yet."));
    app.games.forEach(function (game) {
      var row = el("div", "saved-game-row");
      var open = el("button", "saved-game");
      open.type = "button";
      if (app.view && app.view.id === game.id) open.setAttribute("aria-current", "true");
      open.appendChild(el("strong", "", game.name));
      open.appendChild(el("small", "", game.title + " · " + game.time + " · turn " + game.turn + (game.ended ? " · ended" : "")));
      open.addEventListener("click", function () { openGame(game.id, true); });
      row.appendChild(open);
      var archive = el("button", "button button-quiet archive-game", "Archive");
      archive.type = "button";
      archive.setAttribute("aria-label", "Archive " + game.name);
      archive.addEventListener("click", function () { archiveGame(game); });
      row.appendChild(archive);
      list.appendChild(row);
    });
  }

  function syncPitch(fillName) {
    var story = app.stories.filter(function (s) { return s.id === $("new-chronicle-story").value; })[0];
    if (!story) return;
    setText("new-chronicle-pitch", story.pitch);
    if (fillName || !$("new-chronicle-name").value) $("new-chronicle-name").value = story.name;
  }

  async function refreshGames() {
    var data = await api("/api/bootstrap");
    app.games = data.games;
    app.stories = data.stories;
    app.settings = data.settings;
    renderDrawer();
    return data;
  }

  async function openGame(id, closeAfter) {
    try {
      app.selected.clear();
      app.recipient = "npc";
      render(await api("/api/games/" + id));
      remember(id);
      renderDrawer();
      if (closeAfter) toggleDialog("chronicles-drawer", "drawer-backdrop", "chronicles-button", false);
    } catch (error) {
      toast(error.message, true);
    }
  }

  async function newChronicle(event) {
    event.preventDefault();
    try {
      var view = await api("/api/games", "POST", { story: $("new-chronicle-story").value, name: $("new-chronicle-name").value });
      render(view);
      remember(view.id);
      await refreshGames();
      toggleDialog("chronicles-drawer", "drawer-backdrop", "chronicles-button", false);
      $("composer-input").focus();
    } catch (error) { toast(error.message, true); }
  }

  async function archiveGame(game) {
    try {
      await api("/api/games/" + game.id, "DELETE");
      toast("Archived " + game.name + ". Its file was moved to the archive folder, not deleted.");
      await refreshGames();
      if (app.view && app.view.id === game.id) window.location.reload();
    } catch (error) { toast(error.message, true); }
  }

  // Settings -----------------------------------------------------------------------------

  var PROVIDERS = ["mistral", "openai"];
  var ROLES = ["storyteller", "director"];

  function settingsStatus(kind, message) {
    var node = $("settings-status");
    node.hidden = !message;
    node.setAttribute("data-kind", kind || "info");
    node.textContent = message || "";
  }

  function fillSelect(node, options, value) {
    empty(node);
    options.forEach(function (item) {
      var option = el("option", "", item.label);
      option.value = item.value;
      node.appendChild(option);
    });
    node.value = value;
  }

  function renderSettings() {
    var s = app.settings;
    PROVIDERS.forEach(function (provider) {
      $(provider + "-model").value = s.profiles[provider].model;
      fillSelect($(provider + "-reasoning"), s.reasoning_options[provider].map(function (v) { return { value: v, label: title(v) }; }), s.profiles[provider].reasoning_effort);
      var badge = $(provider + "-credential-status");
      badge.textContent = s.credentials[provider] ? "PROVIDED" : "NOT PROVIDED";
      badge.setAttribute("data-state", s.credentials[provider] ? "provided" : "missing");
    });
    ROLES.forEach(function (role) {
      var options = PROVIDERS.map(function (provider) {
        return { value: provider, label: s.providers[provider] + " · " + s.profiles[provider].model };
      });
      options.push({ value: "offline", label: "Offline: canned script, no model" });
      fillSelect($("role-" + role), options, s.roles[role]);
    });
  }

  function readSettingsForm() {
    var profiles = {}, roles = {};
    PROVIDERS.forEach(function (provider) {
      profiles[provider] = { model: $(provider + "-model").value.trim(), reasoning_effort: $(provider + "-reasoning").value };
    });
    ROLES.forEach(function (role) { roles[role] = $("role-" + role).value; });
    return { profiles: profiles, roles: roles };
  }

  async function reloadSettings(message) {
    try {
      app.settings = await api("/api/settings");
      renderSettings();
      settingsStatus("info", message || "");
    } catch (error) { settingsStatus("error", error.message); }
  }

  async function saveSettings(event) {
    event.preventDefault();
    try {
      app.settings = await api("/api/settings", "POST", readSettingsForm());
      renderSettings();
      settingsStatus("success", "Saved. Future requests use these settings.");
      toast("Settings saved.");
    } catch (error) { settingsStatus("error", error.message); }
  }

  // Start --------------------------------------------------------------------------------

  function bind() {
    $("composer-form").addEventListener("submit", submitComposer);
    $("composer-input").addEventListener("input", updateCount);
    $("composer-input").addEventListener("keydown", function (event) {
      if (event.key === "Enter" && !event.shiftKey && !event.ctrlKey && !event.isComposing) {
        event.preventDefault();
        $("composer-form").requestSubmit();
      } else if (event.key === "Enter" && event.ctrlKey) {
        event.preventDefault();
        var input = event.target, at = input.selectionStart;
        input.setRangeText("\n", at, input.selectionEnd, "end");
        updateCount();
      }
    });
    $("recipient-npc").addEventListener("click", function () { app.recipient = "npc"; renderControls(); });
    $("recipient-storyteller").addEventListener("click", function () { app.recipient = "storyteller"; renderControls(); });
    $("dialogue-exit").addEventListener("click", exitDialogue);
    $("reroll-button").addEventListener("click", function () { rollAction("reroll"); });
    $("resolve-button").addEventListener("click", function () { rollAction("accept"); });
    $("continue-button").addEventListener("click", function () { send("I press on.", "storyteller"); });
    $("clarification-revise").addEventListener("click", function () {
      $("clarification-panel").hidden = true;
      $("composer-input").focus();
    });
    $("debug-panel").addEventListener("toggle", function () { if ($("debug-panel").open) loadNotebook(); });
    $("chronicles-button").addEventListener("click", function () {
      refreshGames().catch(function (error) { toast(error.message, true); });
      toggleDialog("chronicles-drawer", "drawer-backdrop", "chronicles-button", true, "close-drawer");
    });
    $("close-drawer").addEventListener("click", function () { toggleDialog("chronicles-drawer", "drawer-backdrop", "chronicles-button", false); });
    $("drawer-backdrop").addEventListener("click", function () { toggleDialog("chronicles-drawer", "drawer-backdrop", "chronicles-button", false); });
    $("new-chronicle-form").addEventListener("submit", newChronicle);
    $("new-chronicle-story").addEventListener("change", function () { syncPitch(true); });
    $("settings-button").addEventListener("click", function () {
      toggleDialog("settings-panel", "settings-backdrop", "settings-button", true, "close-settings");
      reloadSettings();
    });
    $("close-settings").addEventListener("click", function () { toggleDialog("settings-panel", "settings-backdrop", "settings-button", false); });
    $("settings-backdrop").addEventListener("click", function () { toggleDialog("settings-panel", "settings-backdrop", "settings-button", false); });
    $("settings-form").addEventListener("submit", saveSettings);
    $("settings-discard").addEventListener("click", function () { renderSettings(); settingsStatus("info", "Unsaved edits discarded."); });
    $("settings-reload").addEventListener("click", function () { reloadSettings("Loaded from the server."); });
    document.addEventListener("keydown", function (event) {
      if (event.key !== "Escape") return;
      if ($("chronicles-drawer").classList.contains("is-open")) toggleDialog("chronicles-drawer", "drawer-backdrop", "chronicles-button", false);
      if ($("settings-panel").classList.contains("is-open")) toggleDialog("settings-panel", "settings-backdrop", "settings-button", false);
    });
  }

  async function start() {
    bind();
    renderControls();
    try {
      var data = await refreshGames();
      var last = remembered();
      var known = data.games.filter(function (g) { return g.id === last; })[0] || data.games[0];
      if (known) await openGame(known.id);
      else toggleDialog("chronicles-drawer", "drawer-backdrop", "chronicles-button", true, "new-chronicle-name");
    } catch (error) {
      toast(error.message, true);
    }
  }

  start();
})();
