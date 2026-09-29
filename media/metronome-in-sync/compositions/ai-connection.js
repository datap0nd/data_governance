// One SVG source for the film and the offline video compositor.
export function connectionSVG(time, assets) {
  const clamp = x => Math.max(0, Math.min(1, x));
  const ease = x => 1 - Math.pow(1 - clamp(x), 3);
  const alpha = (start, duration = .35) => ease((time - start) / duration);
  const place = (svg, x, y, size) => svg.replace(/<svg\b[^>]*>/, tag => tag.replace(/\s(?:width|height|style|class|x|y)="[^"]*"/g, '').replace('<svg', `<svg x="${x}" y="${y}" width="${size}" height="${size}"`));
  const names = ['ChatGPT', 'Claude', 'Gemini'];
  const centers = [115, 355, 595];
  const paths = ['M115 278V318Q115 334 131 334H339Q355 334 355 350V405', 'M355 278V405', 'M595 278V318Q595 334 579 334H371Q355 334 355 350V405'];
  const lengths = [353.266, 127, 353.266];
  const tools = centers.map((x, i) => {
    const p = alpha(63.65 + i * .13);
    return `<g opacity="${p}" transform="translate(0 ${(1-p)*18})"><circle cx="${x}" cy="162" r="65" fill="#fff" stroke="#d2ded8" stroke-width="2"/>${place(assets.logos[i], x-39, 123, 78)}<text x="${x}" y="255" text-anchor="middle" font-size="28" font-weight="500">${names[i]}</text></g>`;
  }).join('');
  const wires = paths.map((d, i) => {
    const p = alpha(64.45 + i * .12, .9), len = lengths[i];
    return `<path d="${d}" fill="none" stroke="#368876" stroke-width="4" stroke-linecap="round" stroke-dasharray="${len}" stroke-dashoffset="${(1-p)*len}" opacity="${p > 0 ? 1 : 0}"/>`;
  }).join('');
  const connected = alpha(65.6, .35);
  const pulse = time >= 65.6 ? (time-65.6)%1.7/1.7 : 0;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="710" height="710" viewBox="0 0 710 710" font-family="Outfit, sans-serif" color="#16333a" fill="#16333a" aria-label="ChatGPT, Claude and Gemini connect to Metronome MCP">
    <text x="10" y="34" font-size="36" font-weight="500" opacity="${alpha(63.4)}">Through your AI</text>
    ${wires}${tools}
    <g opacity="${alpha(64.35, .4)}">
      <circle cx="355" cy="484" r="79" fill="#fff" stroke="#d2ded8" stroke-width="2"/>
      <circle cx="355" cy="484" r="${82+pulse*16}" fill="none" stroke="#328d75" stroke-width="2" opacity="${connected*(1-pulse)*.3}"/>
      <circle cx="355" cy="484" r="79" fill="#e8f4ee" fill-opacity="${connected}" stroke="#328d75" stroke-width="3" stroke-opacity="${connected}"/>
      ${place(assets.mark, 307, 436, 96)}
      <text x="355" y="621" text-anchor="middle" font-size="40" font-weight="500">Metronome MCP</text>
    </g>
  </svg>`;
}
