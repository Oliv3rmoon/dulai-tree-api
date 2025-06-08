(() => {
  /* CONFIG -------------------------------------------------- */
  const scriptTag = document.currentScript;
  const API_URL   = scriptTag.getAttribute("data-api");
  const TITLE     = scriptTag.getAttribute("data-title")   || "Chat";
  const ACCENT    = scriptTag.getAttribute("data-accent")  || "#1b9b3a";

  /* STYLES (auto-injected) ---------------------------------- */
  const css = `
    .dulai-bubble { position:fixed; bottom:24px; right:24px; width:64px; height:64px;
      border-radius:50%; background:${ACCENT}; color:#fff; font-size:28px;
      display:flex; align-items:center; justify-content:center; cursor:pointer;
      box-shadow:0 4px 12px rgba(0,0,0,.2); z-index:9998; }
    .dulai-panel  { position:fixed; bottom:96px; right:24px; width:320px; height:420px;
      border-radius:12px; background:#fff; box-shadow:0 8px 24px rgba(0,0,0,.25);
      display:flex; flex-direction:column; overflow:hidden; z-index:9999;
      transform:translateY(120%); transition:transform .3s; }
    .dulai-panel.open { transform:translateY(0); }
    .dulai-header{ background:${ACCENT}; color:#fff; padding:12px; font-weight:600; }
    .dulai-msgs  { flex:1; padding:12px; overflow-y:auto; font:14px/1.4 sans-serif; }
    .dulai-input { display:flex; border-top:1px solid #eee; }
    .dulai-input textarea{ flex:1; resize:none; border:none; padding:8px; font:14px sans-serif; }
    .dulai-input button{ background:${ACCENT}; color:#fff; border:none; padding:0 16px;
      cursor:pointer; }
    .dulai-bot , .dulai-user{ margin:6px 0; white-space:pre-wrap; }
    .dulai-bot  { color:#111; }
    .dulai-user { color:${ACCENT}; text-align:right; }
  `;
  const style = document.createElement("style");
  style.textContent = css;
  document.head.appendChild(style);

  /* DOM ------------------------------------------------------ */
  const bubble = document.createElement("div");
  bubble.className = "dulai-bubble";
  bubble.textContent = "💬";
  document.body.appendChild(bubble);

  const panel = document.createElement("div");
  panel.className = "dulai-panel";
  panel.innerHTML = `
     <div class="dulai-header">${TITLE}</div>
     <div class="dulai-msgs" id="dulai-msgs"></div>
     <form class="dulai-input">
       <textarea rows="1" placeholder="Type a message…" required></textarea>
       <button type="submit">⮞</button>
     </form>`;
  document.body.appendChild(panel);

  const msgs    = panel.querySelector("#dulai-msgs");
  const form    = panel.querySelector("form");
  const textarea= form.querySelector("textarea");

  /* HELPERS -------------------------------------------------- */
  const add = (text, cls) => {
    const div = document.createElement("div");
    div.className = cls;
    div.textContent = text.trim();
    msgs.appendChild(div);
    msgs.scrollTop = msgs.scrollHeight;
  };

  /* CHAT LOGIC ---------------------------------------------- */
  async function sendMessage(text) {
    add(text, "dulai-user");
    textarea.value = "";
    textarea.disabled = true;

    const res = await fetch(API_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text })
    });

    if (!res.ok || !res.body) {
      add("Sorry, connection error.", "dulai-bot");
      textarea.disabled = false;
      return;
    }

    const reader = res.body.getReader();
    let botLine = "";
    add("...", "dulai-bot");
    const lastDiv = msgs.lastChild;

    const decoder = new TextDecoder();
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const chunk = decoder.decode(value, { stream: true });
      chunk.split(/\r?\n/).forEach(line => {
        if (!line.trim()) return;
        // each chunk is JSON with {content?}
        try {
          const parsed = JSON.parse(line);
          if (parsed.content) {
            botLine += parsed.content;
            lastDiv.textContent = botLine.trim();
            msgs.scrollTop = msgs.scrollHeight;
          }
        } catch (e) {/* ignore */ }
      });
    }
    textarea.disabled = false;
    textarea.focus();
  }

  /* EVENTS --------------------------------------------------- */
  bubble.onclick = () => panel.classList.toggle("open");
  form.onsubmit = e => {
    e.preventDefault();
    if (textarea.value.trim()) sendMessage(textarea.value.trim());
  };
})();
