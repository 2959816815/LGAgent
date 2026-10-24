function escapeHtml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function inline(text: string): string {
  const codes: string[] = [];
  let value = text.replace(/`([^`]+)`/g, (_match, code: string) => {
    codes.push(`<code>${escapeHtml(code)}</code>`);
    return `\u0000${codes.length - 1}\u0000`;
  });

  value = escapeHtml(value)
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>')
    .replace(/(^|[\s(])(https?:\/\/[^\s<]+)/g, '$1<a href="$2" target="_blank" rel="noreferrer">$2</a>');

  return value.replace(/\u0000(\d+)\u0000/g, (_match, index: string) => codes[Number(index)] ?? "");
}

export function markdownToHtml(source: string): string {
  const lines = source.replace(/\r\n/g, "\n").split("\n");
  let html = "";
  let list: string[] = [];
  let inCode = false;
  let code: string[] = [];

  const flushList = () => {
    if (list.length === 0) return;
    html += `<ul>${list.join("")}</ul>`;
    list = [];
  };

  for (const line of lines) {
    if (line.startsWith("```")) {
      if (inCode) {
        html += `<pre><code>${escapeHtml(code.join("\n"))}</code></pre>`;
        code = [];
        inCode = false;
      } else {
        flushList();
        inCode = true;
      }
      continue;
    }
    if (inCode) {
      code.push(line);
      continue;
    }

    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    if (heading) {
      flushList();
      const level = Math.min(heading[1].length + 1, 6);
      html += `<h${level}>${inline(heading[2])}</h${level}>`;
      continue;
    }

    const bullet = /^\s*[-*]\s+(.*)$/.exec(line);
    if (bullet) {
      list.push(`<li>${inline(bullet[1])}</li>`);
      continue;
    }

    flushList();

    if (!line.trim()) continue;
    if (/^>\s?/.test(line)) {
      html += `<blockquote>${inline(line.replace(/^>\s?/, ""))}</blockquote>`;
    } else {
      html += `<p>${inline(line)}</p>`;
    }
  }

  if (inCode) html += `<pre><code>${escapeHtml(code.join("\n"))}</code></pre>`;
  flushList();
  return html;
}
