import { useEffect, useRef, useState } from "react";
import ChatMarkdown from "./ChatMarkdown";

// React port of studio.py's _chat_html — the connector-selection cascade and
// the actual model calls all happen server-side (_handle_chat_message);
// this just renders whatever `wf.messages` the API hands back and posts new
// text to onSend.
function WelcomeBubbles({ wf }) {
  if (wf.connector === null || wf.connector === undefined) {
    return (
      <>
        <div className="chat-bubble chat-bubble-assistant">
          Tell me what you'd like built and I'll put the interface together for you — afterward you can ask me
          to update its data or add new actions to it any time. If you'd rather connect a real app directly,
          just type its name.
        </div>
        <div className="chat-bubble chat-bubble-assistant">So, what would you like to build?</div>
      </>
    );
  }
  return (
    <div className="chat-bubble chat-bubble-assistant">
      Hi, I'm Pilant Studio. Describe the interface you want and I'll build it from your connected{" "}
      {wf.connector_label || "app"}.
      {wf.connector === "gmail"
        ? " Your live inbox is on the right — star, delete, and compose work directly from there."
        : ""}
    </div>
  );
}

export default function ChatPane({ wf, onSend, sending }) {
  const [text, setText] = useState("");
  const scrollRef = useRef(null);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [wf.messages]);

  const submit = (e) => {
    e.preventDefault();
    const trimmed = text.trim();
    if (!trimmed || sending) return;
    setText("");
    onSend(trimmed);
  };

  return (
    <div className="chatpane">
      <div className="chat-scroll" ref={scrollRef}>
        {wf.messages.length === 0 ? (
          <WelcomeBubbles wf={wf} />
        ) : (
          wf.messages.map((m, i) => (
            <div key={i} className={`chat-bubble chat-bubble-${m.role}`}>
              <ChatMarkdown text={m.text} />
            </div>
          ))
        )}
      </div>
      <form className="chat-form" onSubmit={submit}>
        <input
          type="text"
          placeholder="Describe the interface you want..."
          autoComplete="off"
          value={text}
          onChange={(e) => setText(e.target.value)}
          disabled={sending}
        />
        <button type="submit" disabled={sending}>
          {sending ? "…" : "Send"}
        </button>
      </form>
    </div>
  );
}
