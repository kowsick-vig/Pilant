// React port of studio.py's _render_chat_markdown — deliberately not a
// general markdown library, just the two things the connectors' clarifying
// questions actually use: **bold** spans, and "1. ..." numbered lines
// grouped into a real <ol>. Built as React elements (not dangerouslySetInnerHTML)
// so there's no escaping to get right — React handles that for free.

const NUMBERED_RE = /^\d+\.\s+(.*)$/;
const BOLD_RE = /\*\*([^*]+)\*\*/g;

function renderInline(text) {
  const parts = [];
  let lastIndex = 0;
  let m;
  BOLD_RE.lastIndex = 0;
  let key = 0;
  while ((m = BOLD_RE.exec(text)) !== null) {
    if (m.index > lastIndex) parts.push(<span key={key++}>{text.slice(lastIndex, m.index)}</span>);
    parts.push(<strong key={key++}>{m[1]}</strong>);
    lastIndex = m.index + m[0].length;
  }
  if (lastIndex < text.length) parts.push(<span key={key++}>{text.slice(lastIndex)}</span>);
  return parts;
}

export default function ChatMarkdown({ text }) {
  const lines = (text || "").split("\n");
  const blocks = [];
  let listBuffer = [];
  let paraBuffer = [];

  const flushList = () => {
    if (listBuffer.length) {
      blocks.push(
        <ol key={blocks.length}>
          {listBuffer.map((item, i) => (
            <li key={i}>{renderInline(item)}</li>
          ))}
        </ol>
      );
      listBuffer = [];
    }
  };
  const flushPara = () => {
    if (paraBuffer.length) {
      blocks.push(
        <p key={blocks.length}>
          {paraBuffer.map((line, i) => (
            <span key={i}>
              {i > 0 ? <br /> : null}
              {renderInline(line)}
            </span>
          ))}
        </p>
      );
      paraBuffer = [];
    }
  };

  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (line === "") {
      flushList();
      flushPara();
      continue;
    }
    const numbered = line.match(NUMBERED_RE);
    if (numbered) {
      flushPara();
      listBuffer.push(numbered[1]);
    } else {
      flushList();
      paraBuffer.push(line);
    }
  }
  flushList();
  flushPara();

  return <>{blocks}</>;
}
