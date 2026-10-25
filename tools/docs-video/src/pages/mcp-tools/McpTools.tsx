// The video on the MCP Tools page (docs/12-mcp-tools.md): a defk becomes a tool an agent calls, and the call runs as
// a doeff program under the handlers of the program that launched the agent. Five scenes and an outro, each with
// headlines a synthetic voice reads (narration.json):
//   1. The idea: an agent's tool call runs as a program, and its effects reach your handlers.
//   2. defmcp-tool wraps a defk: an McpToolDef with a name, a JSON Schema and a @do handler.
//   3. Launch with tools: claude_agent_handler captures the handlers, starts the MCP server, writes .mcp.json,
//      starts the agent in a terminal and resumes k with a session handle.
//   4. The calls: tools/list, query-price-tool answered by paper_broker, a call refused by the schema, an order.
//   5. Same VM, same handlers: the program sees the agent's order after Stop, in either handler order.
// Every value on screen is printed by a program in measure/ (its output is in the .log next to it). No agent model
// runs there: the terminal is a fake and a script replays an agent's calls over the real MCP server.
import React from 'react';
import {AbsoluteFill, Easing, interpolate} from 'remotion';
import {clamp, fadeAt, mix, ramp} from '../../kit/motion';
import {narrate} from '../../kit/narrated';
import {CodeBlock, glow, Headlines, lineMid, Lines, Pill, startsOf} from '../../kit/parts';
import {ACCENT, f, FAIL, LOOK, MONO, MUTED, PANEL, PROD, SANS, TEST, TEXT} from '../../kit/theme';
import {useSceneFrame} from '../../kit/warp';
import DURATIONS from './durations.json';

// A label in the page's sans font, placed at (x, y).
const Label: React.FC<{x: number; y: number; color?: string; size?: number; bold?: boolean; opacity?: number; children: React.ReactNode}> = ({
  x, y, color = MUTED, size = 20, bold = true, opacity = 1, children,
}) => (
  <div style={{position: 'absolute', left: x, top: y, fontFamily: SANS, fontSize: size, fontWeight: bold ? 700 : 400, color, whiteSpace: 'nowrap', opacity}}>
    {children}
  </div>
);

// A dark panel of output lines in the mono font; a line shows from its second on (or always, without one).
type Out = {text: string; color?: string; at?: number};
const Terminal: React.FC<{x: number; y: number; w: number; lines: Out[]; size?: number; line?: number; opacity?: number; title?: string}> = ({
  x, y, w, lines, size = 15, line = 24, opacity = 1, title,
}) => {
  const frame = useSceneFrame();
  return (
    <div
      style={{
        position: 'absolute', left: x, top: y, width: w, boxSizing: 'border-box', padding: '10px 16px', background: '#0b0d13', borderRadius: 10,
        border: '2px solid #2a2e40', fontFamily: MONO, fontSize: size, lineHeight: `${line}px`, color: TEXT, opacity,
      }}
    >
      {title && <div style={{fontFamily: SANS, fontSize: 14, color: MUTED, lineHeight: '22px'}}>{title}</div>}
      {lines.map((l, i) => (
        <div key={i} style={{whiteSpace: 'pre', color: l.color ?? TEXT, minHeight: line, opacity: l.at === undefined ? 1 : fadeAt(frame, l.at, 0.25)}}>
          {l.text}
        </div>
      ))}
    </div>
  );
};

// A box with a title: a handler, a server, the agent's side.
const Frame: React.FC<{
  x: number; y: number; w: number; h: number; color: string; title: string; note?: string; on?: boolean; opacity?: number; size?: number; dashed?: boolean;
  children?: React.ReactNode;
}> = ({x, y, w, h, color, title, note, on = false, opacity = 1, size = 20, dashed = false, children}) => (
  <div
    style={{
      position: 'absolute', left: x, top: y, width: w, height: h, boxSizing: 'border-box', borderRadius: 14, background: PANEL,
      border: `3px ${dashed ? 'dashed' : 'solid'} ${color}`, ...glow(on, color), opacity,
    }}
  >
    <div style={{position: 'absolute', left: 16, top: 8, fontFamily: MONO, fontSize: size, fontWeight: 700, color: on ? LOOK : color, whiteSpace: 'nowrap'}}>{title}</div>
    {note && <div style={{position: 'absolute', left: 16, top: 14 + size, fontFamily: SANS, fontSize: 15, color: MUTED, whiteSpace: 'nowrap'}}>{note}</div>}
    {children}
  </div>
);

// A pill on the move from one point to another between two seconds, gone shortly after it arrives.
const Travel: React.FC<{from: [number, number]; to: [number, number]; at: number; until: number; text: string; color: string; stay?: number}> = ({
  from, to, at, until, text, color, stay = 0.3,
}) => {
  const frame = useSceneFrame();
  if (frame < f(at) || frame >= f(until + stay)) return null;
  const t = ramp(frame, at, until);
  return <Pill x={mix(from[0], to[0], t)} y={mix(from[1], to[1], t)} text={text} color={color} opacity={fadeAt(frame, at, 0.15) * (1 - ramp(frame, until + stay - 0.2, until + stay))} />;
};

// Scene 1 — the idea: a tool call runs as a program, and its effects go out to your handlers.

const IDEA_LINES: Lines = [
  [0, 'MCP Tools: a defk becomes a tool an agent can call.'],
  [6, "The agent's call runs as a doeff program. Its effects go out to your handlers."],
  [14, 'So a tool reaches the same handlers as the rest of your program.'],
];
const IDEA = f(20);

const IdeaScene: React.FC = () => {
  const frame = useSceneFrame();
  const agent = {x: 60, y: 190, w: 300, h: 200};
  const tool = {x: 470, y: 150, w: 350, h: 130};
  const prog = {x: 470, y: 380, w: 330, h: 130};
  const hand = {x: 910, y: 150, w: 310, h: 360};
  const toolOn = frame >= f(7.6) && frame < f(11.6);
  const handOn = (frame >= f(8.8) && frame < f(10.0)) || (frame >= f(16.0) && frame < f(17.2));
  const progOn = frame >= f(15.0) && frame < f(18.0);
  return (
    <AbsoluteFill>
      <Headlines lines={IDEA_LINES} />
      <Frame {...agent} color={PROD} title="an agent" note="Claude Code, in a terminal" opacity={fadeAt(frame, 0.6)}>
        <div style={{position: 'absolute', left: 16, top: 80, fontFamily: MONO, fontSize: 15, lineHeight: '24px', color: TEXT, whiteSpace: 'pre'}}>
          {'> Check 7203 price,\n  buy 100 shares\n  if under 2500.'}
        </div>
      </Frame>
      <Frame {...tool} color={ACCENT} title="submit-order-tool" note="a tool: your defk, with a schema" on={toolOn} opacity={fadeAt(frame, 1.6)}>
        <div style={{position: 'absolute', left: 16, top: 76, fontFamily: MONO, fontSize: 16, color: TEXT, whiteSpace: 'nowrap'}}>
          (submit-order symbol side qty)
        </div>
      </Frame>
      <Frame {...hand} color={TEST} title="your handlers" note="installed around your program" on={handOn} opacity={fadeAt(frame, 2.6)}>
        {['paper_broker', 'state', 'scheduled'].map((name, i) => (
          <div
            key={name}
            style={{
              position: 'absolute', left: 16 + i * 8, top: 92 + i * 60, width: 262 - i * 16, height: 46, boxSizing: 'border-box', borderRadius: 10,
              border: `2px solid ${TEST}`, fontFamily: MONO, fontSize: 17, fontWeight: 700, color: TEST, lineHeight: '42px', paddingLeft: 14,
            }}
          >
            {name}
          </div>
        ))}
      </Frame>
      <Frame {...prog} color={ACCENT} title="trading_agent()" note="the program that launched the agent" on={progOn} opacity={fadeAt(frame, 14.2)} />
      {/* The call: agent → tool → its effect out to the handlers → the answer back to the agent. */}
      <Travel from={[agent.x + agent.w, agent.y + 70]} to={[tool.x, tool.y + 70]} at={6.4} until={7.6} text="tools/call" color={PROD} />
      <Travel from={[tool.x + tool.w, tool.y + 50]} to={[hand.x, hand.y + 115]} at={7.8} until={8.9} text="PlaceOrder(...)" color={ACCENT} />
      <Travel from={[hand.x, hand.y + 140]} to={[tool.x + tool.w, tool.y + 95]} at={10.0} until={11.0} text="filled" color={TEST} />
      <Travel from={[tool.x + 60, tool.y + tool.h + 24]} to={[agent.x + agent.w / 2 + 60, agent.y + agent.h + 34]} at={11.4} until={12.4} text="'filled: buy 100 of 7203 at 2480'" color={TEST} stay={1.2} />
      {/* The program asks the same handler, and sees the same book. */}
      <Travel from={[prog.x + prog.w, prog.y + 50]} to={[hand.x, hand.y + 115]} at={15.2} until={16.2} text="Position('7203')" color={ACCENT} />
      <Travel from={[hand.x, hand.y + 150]} to={[prog.x + prog.w, prog.y + 95]} at={17.0} until={18.0} text="100" color={TEST} stay={2.0} />
      <Label x={prog.x} y={prog.y + prog.h + 14} size={18} color={TEXT} opacity={fadeAt(frame, 18.0)}>
        the same paper_broker, the same 100 shares
      </Label>
    </AbsoluteFill>
  );
};

// Scene 2 — defmcp-tool wraps a defk: an McpToolDef with a name, a JSON Schema and a @do handler.

const DEFINE_LINES: Lines = [
  [0, 'defmcp-tool wraps an existing defk in a tool.'],
  [6, 'It expands to an McpToolDef: a name and a JSON Schema the agent reads.'],
  [14, 'Its handler is a @do function. Run it, and the defk runs.'],
];
const DEFINE = f(22);
// The page's example, as measure/define_tool.hy runs it.
const HY = [
  '(defk submit-order [symbol side qty]',
  '  {:pre [(: symbol str) (: side str) (: qty int)] :post [(: % str)]}',
  '  f"accepted: {side} {qty} of {symbol}")',
  ' ',
  '(defmcp-tool submit-order-tool',
  '  "Submit a trading order to Kabustation"',
  '  [{:name "symbol" :type "string" :description "Stock symbol code (e.g. 7203)"}',
  '   {:name "side" :type "string" :enum ["buy" "sell"] :description "Order side"}',
  '   {:name "qty" :type "integer" :description "Quantity to trade"}]',
  '  (<- result (submit-order symbol side qty))',
  '  result)',
];
// From measure/define_tool.log: the McpToolDef the macro made.
const SCHEMA: Out[] = [
  {text: 'name: "submit-order-tool"', color: ACCENT, at: 6.6},
  {text: 'input_schema:', color: MUTED, at: 7.4},
  {text: '  symbol: {"type": "string"}', at: 8.0},
  {text: '  side:   {"type": "string", "enum": ["buy", "sell"]}', at: 9.0},
  {text: '  qty:    {"type": "integer"}', at: 10.0},
  {text: '  required: ["symbol", "side", "qty"]', at: 11.0},
];

const DefineScene: React.FC = () => {
  const frame = useSceneFrame();
  const code = {x: 50, y: 84, w: 1180, size: 16, line: 25};
  // The band: the defk, then the tool's name, its three parameters, and (when run) the body.
  const steps: [number, number][] = [[1.0, 0], [3.0, 4], [6.6, 4], [8.0, 6], [9.0, 7], [10.0, 8], [15.2, 9]];
  const step = steps.reduce((found, [at], i) => (frame >= f(at) ? i : found), -1);
  const band = step < 0 ? null : step === 0 ? 0 : mix(steps[step - 1][1], steps[step][1], ramp(frame, steps[step][0], steps[step][0] + 0.3));
  const panelY = 420;
  const made = frame >= f(6.0) && frame < f(12.0);
  return (
    <AbsoluteFill>
      <Headlines lines={DEFINE_LINES} />
      <CodeBlock lines={HY} {...code} band={band} dim={(i) => (i < 3 ? mix(1, 0.45, ramp(frame, 3.0, 3.4)) * mix(1, 1 / 0.45, ramp(frame, 15.2, 15.6)) : 1)} opacity={fadeAt(frame, 0.2)} />
      <Label x={60} y={panelY - 30} size={17} color={MUTED} opacity={fadeAt(frame, 1.6) * (1 - ramp(frame, 5.6, 6.0))}>
        submit-order is a defk you already have; the tool gets a name of its own.
      </Label>
      <Frame x={50} y={panelY} w={640} h={280} color={ACCENT} title="McpToolDef" note="what the agent sees" on={made} opacity={fadeAt(frame, 6.0)}>
        <Terminal x={14} y={66} w={606} lines={SCHEMA} size={15} line={26} />
      </Frame>
      <Frame x={720} y={panelY} w={510} h={280} color={TEST} title=".handler" note="a @do function, the tool's program" on={frame >= f(15.0) && frame < f(17.6)} opacity={fadeAt(frame, 14.0)}>
        <Terminal
          x={14} y={66} w={476} size={14} line={24}
          lines={[
            {text: '_submit_order_tool_mcp_handler(', color: TEXT, at: 14.4},
            {text: "    symbol: 'str', side: 'str', qty: 'int')", color: MUTED, at: 14.4},
            {text: ' ', at: 15.0},
            {text: '(run (submit-order-tool.handler', color: MUTED, at: 15.0},
            {text: '       "7203" "buy" 100))', color: MUTED, at: 15.0},
            {text: "→ 'accepted: buy 100 of 7203'", color: TEST, at: 16.6},
          ]}
        />
      </Frame>
    </AbsoluteFill>
  );
};

// Scene 3 — Launch with tools: what claude_agent_handler does before it resumes k.

const LAUNCH_LINES: Lines = [
  [0, 'A program yields Launch, with the tools the agent may call.'],
  [6.5, 'claude_agent_handler catches Launch, captures handlers, starts an MCP server.'],
  [14, 'It writes .mcp.json, starts Claude Code in a terminal, and types the prompt.'],
  [20, 'Then it resumes k with a session handle.'],
  [25, 'In this run the terminal is a fake, and a script plays the agent.'],
];
const LAUNCH = f(31);
// measure/agent_flow.py: the program and the stack it runs under.
const PROGRAM = [
  '@do',
  'def trading_agent(work_dir):',
  '    handle = yield Launch(',
  '        "trader",',
  '        agent_type=AgentType.CLAUDE,',
  '        work_dir=work_dir,',
  '        prompt="Check 7203 price, buy 100 shares if under 2500.",',
  '        mcp_tools=(query_price_tool, submit_order_tool),',
  '    )',
];
const STACK = [
  {name: 'scheduled', color: TEST},
  {name: 'state()', color: TEST},
  {name: 'claude_agent_handler()', color: PROD},
  {name: 'paper_broker', color: TEST},
  {name: 'trading_agent(work_dir)', color: ACCENT},
];
// measure/agent_flow.log, milliseconds after run() started.
const LAUNCH_LOG: Out[] = [
  {text: '282.6 ms  program yields Launch', color: ACCENT, at: 2.4},
  {text: '502.6 ms  terminal: new session trader', color: MUTED, at: 16.0},
  {text: '502.9 ms  terminal: typed claude', color: MUTED, at: 16.8},
  {text: "503.2 ms  terminal: typed 'Check 7203 price, buy 100 shares if under 2500.'", color: MUTED, at: 17.6},
  {text: "503.3 ms  program resumed with SessionHandle('trader')", color: TEST, at: 21.6},
  {text: '504.2 ms  agent reads .mcp.json: {"mcpServers": {"doeff": {"type": "sse", "url": "http://127.0.0.1:42229/sse"}}}', color: PROD, at: 26.4},
];

const LaunchScene: React.FC = () => {
  const frame = useSceneFrame();
  const code = {x: 40, y: 84, w: 640, size: 15, line: 26};
  const rowX = 720;
  const rowY = (i: number) => 92 + i * 52;
  const rowW = (i: number) => 300 - i * 12;
  const claude = 2;
  const broker = 3;
  const caught = frame >= f(5.6) && frame < f(22.0);
  const capture = ramp(frame, 8.6, 9.6);
  const awayDim = interpolate(frame, [f(2.4), f(2.8), f(21.6), f(22.0)], [1, 0.4, 0.4, 1], clamp);
  return (
    <AbsoluteFill>
      <Headlines lines={LAUNCH_LINES} />
      <CodeBlock lines={PROGRAM} {...code} band={frame >= f(1.0) ? 2 : null} dim={(i) => (i >= 3 ? awayDim : 1)} opacity={fadeAt(frame, 0.2)} />
      <Label x={code.x} y={code.y + 24 + PROGRAM.length * code.line + 8} color={ACCENT} size={17} opacity={fadeAt(frame, 2.8) * (1 - ramp(frame, 21.6, 22.0))}>
        k = the rest of trading_agent  <span style={{color: MUTED, fontWeight: 400}}>paused at the yield</span>
      </Label>
      <Label x={rowX} y={64} size={15} opacity={fadeAt(frame, 0.6)}>run(...) — outermost first</Label>
      {STACK.map((row, i) => {
        const on = (i === claude && caught) || (i === broker && frame >= f(8.6) && frame < f(10.4));
        return (
          <div
            key={row.name}
            style={{
              position: 'absolute', left: rowX + i * 6, top: rowY(i), width: rowW(i), height: 42, boxSizing: 'border-box', borderRadius: 10, background: PANEL,
              border: `2px solid ${row.color}`, ...glow(on, row.color), fontFamily: MONO, fontSize: 16, fontWeight: 700, color: on ? LOOK : row.color,
              lineHeight: '38px', paddingLeft: 12, opacity: fadeAt(frame, 0.6 + i * 0.2),
            }}
          >
            {row.name}
          </div>
        );
      })}
      {/* Launch goes out from the program, passes paper_broker, and is caught by claude_agent_handler. */}
      <Travel from={[rowX + 160, rowY(4) + 21]} to={[rowX + 160, rowY(claude) + 21]} at={3.0} until={5.4} text="Launch(..., mcp_tools=(2 tools))" color={ACCENT} />
      <Label x={rowX + 300} y={rowY(broker) + 8} size={15} bold={false} opacity={fadeAt(frame, 4.0) * (1 - ramp(frame, 6.0, 6.4))}>not its effect: passes</Label>
      {/* The capture: the handlers below claude_agent_handler (GetHandlers(k)) and above it (GetOuterHandlers()). */}
      <div
        style={{
          position: 'absolute', left: rowX - 14, top: rowY(broker) - 6, width: 330, height: 54, boxSizing: 'border-box', borderRadius: 12,
          border: `2px dashed ${LOOK}`, opacity: capture * (1 - ramp(frame, 13.6, 14.0)),
        }}
      />
      <div
        style={{
          position: 'absolute', left: rowX - 14, top: rowY(0) - 6, width: 330, height: 106, boxSizing: 'border-box', borderRadius: 12,
          border: `2px dashed ${LOOK}`, opacity: ramp(frame, 9.6, 10.4) * (1 - ramp(frame, 13.6, 14.0)),
        }}
      />
      <Label x={rowX + 320} y={rowY(broker) + 10} size={14} color={LOOK} opacity={capture * (1 - ramp(frame, 13.6, 14.0))}>GetHandlers(k)</Label>
      <Label x={rowX + 320} y={rowY(0) + 34} size={14} color={LOOK} opacity={ramp(frame, 9.6, 10.4) * (1 - ramp(frame, 13.6, 14.0))}>GetOuterHandlers()</Label>
      {/* What the handler sets up, one card each. */}
      <Frame x={720} y={360} w={160} h={70} color={PROD} title="MCP server" size={15} opacity={fadeAt(frame, 11.2)} on={frame >= f(11.2) && frame < f(12.4)}>
        <div style={{position: 'absolute', left: 16, top: 36, fontFamily: MONO, fontSize: 13, color: TEXT}}>:42229/sse</div>
      </Frame>
      <Frame x={895} y={360} w={160} h={70} color={PROD} title=".mcp.json" size={15} opacity={fadeAt(frame, 14.4)} on={frame >= f(14.4) && frame < f(15.6)}>
        <div style={{position: 'absolute', left: 16, top: 36, fontFamily: MONO, fontSize: 13, color: TEXT}}>{'"doeff": sse'}</div>
      </Frame>
      <Frame
        x={1070} y={360} w={170} h={70} color={PROD} title="terminal" size={15} dashed={frame >= f(25.6)} opacity={fadeAt(frame, 16.0)}
        on={(frame >= f(16.0) && frame < f(18.4)) || (frame >= f(25.6) && frame < f(27.4))}
      >
        <div style={{position: 'absolute', left: 16, top: 36, fontFamily: MONO, fontSize: 13, color: frame >= f(25.6) ? LOOK : TEXT}}>
          {frame >= f(25.6) ? 'a fake' : '$ claude'}
        </div>
      </Frame>
      {/* Resume: the handle goes back down to the program. */}
      <Travel from={[rowX + 160, rowY(claude) + 21]} to={[code.x + 420, lineMid(code.y, code.line, 2)]} at={20.4} until={21.6} text="SessionHandle('trader')" color={TEST} />
      <Terminal x={40} y={452} w={1200} lines={LAUNCH_LOG} size={15} line={24} title="measure/agent_flow.log" opacity={fadeAt(frame, 2.2)} />
      <Label x={40} y={650} size={17} color={LOOK} opacity={fadeAt(frame, 25.8)}>
        No agent model runs: a script reads .mcp.json and makes the calls Claude Code makes.
      </Label>
    </AbsoluteFill>
  );
};

// Scene 4 — the calls: the agent's side, the MCP server, and the program's VM.

const CALL_LINES: Lines = [
  [0, 'The agent reads .mcp.json, connects over SSE, and lists the tools.'],
  [6.5, 'It calls query-price-tool: QuotePrice goes out, and paper_broker answers 2480.'],
  [14.5, 'A call that breaks the schema never reaches the program.'],
  [21.5, 'side "buy" goes through: PlaceOrder reaches paper_broker; the order fills.'],
];
const CALL = f(29);
// measure/agent_flow.log: the agent's requests and what came back.
const CALL_LOG: Out[] = [
  {text: '508.2 ms  POST tools/list', color: PROD, at: 2.2},
  {text: "511.3 ms  ← query-price-tool(symbol)  submit-order-tool(symbol, side, qty)", color: TEXT, at: 4.2},
  {text: '511.5 ms  POST tools/call query-price-tool {"symbol": "7203"}', color: PROD, at: 7.0},
  {text: '514.8 ms  ← {"isError": false, "text": "2480"}', color: TEST, at: 12.0},
  {text: '514.9 ms  POST tools/call submit-order-tool {"symbol": "7203", "side": "hold", "qty": 100}', color: PROD, at: 15.0},
  {text: "517.1 ms  ← {\"isError\": true, \"text\": \"Error: argument side: expected one of ['buy', 'sell']\"}", color: FAIL, at: 17.6},
  {text: '517.2 ms  POST tools/call submit-order-tool {"symbol": "7203", "side": "buy", "qty": 100}', color: PROD, at: 22.0},
  {text: '520.4 ms  ← {"isError": false, "text": "filled: buy 100 of 7203 at 2480"}', color: TEST, at: 26.0},
];

const CallScene: React.FC = () => {
  const frame = useSceneFrame();
  const agent = {x: 40, y: 90, w: 250, h: 300};
  const server = {x: 420, y: 90, w: 250, h: 300};
  const vm = {x: 800, y: 90, w: 440, h: 300};
  const prog = {x: vm.x + 20, y: vm.y + 70, w: vm.w - 40, h: 80};
  const brk = {x: vm.x + 20, y: vm.y + 200, w: vm.w - 40, h: 76};
  const ay = (dy: number) => agent.y + dy;
  // Which call the program box shows.
  const call = frame >= f(21.5) ? 'submit_order("7203", "buy", 100)' : frame >= f(6.5) ? 'query_price("7203")' : '';
  const progOn = (frame >= f(8.6) && frame < f(11.4)) || (frame >= f(23.4) && frame < f(25.6));
  const brkOn = (frame >= f(9.6) && frame < f(10.6)) || (frame >= f(24.2) && frame < f(25.0));
  const refused = frame >= f(16.0) && frame < f(18.6);
  const vmDim = interpolate(frame, [f(15.0), f(15.4), f(19.6), f(20.0)], [1, 0.35, 0.35, 1], clamp);
  return (
    <AbsoluteFill>
      <Headlines lines={CALL_LINES} />
      <Frame {...agent} color={PROD} title="the agent" note="a script, replaying its calls" opacity={fadeAt(frame, 0.2)}>
        <div style={{position: 'absolute', left: 16, top: 80, fontFamily: MONO, fontSize: 14, lineHeight: '22px', color: TEXT, whiteSpace: 'pre', opacity: fadeAt(frame, 1.0)}}>
          {'reads .mcp.json\nGET /sse'}
        </div>
        <div style={{position: 'absolute', left: 16, top: 150, fontFamily: MONO, fontSize: 14, lineHeight: '22px', color: TEXT, whiteSpace: 'pre', opacity: fadeAt(frame, 4.2)}}>
          {'tools:\n  query-price-tool\n  submit-order-tool'}
        </div>
      </Frame>
      <Frame {...server} color={PROD} title="MCP server" note="checks the arguments" on={refused} opacity={fadeAt(frame, 0.4)}>
        <div style={{position: 'absolute', left: 16, top: 80, fontFamily: MONO, fontSize: 14, color: MUTED}}>127.0.0.1:42229</div>
        {frame >= f(16.0) && (
          <div
            style={{
              position: 'absolute', left: 14, top: 120, width: server.w - 34, boxSizing: 'border-box', padding: '8px 10px', borderRadius: 10, border: `2px solid ${FAIL}`,
              fontFamily: MONO, fontSize: 13, lineHeight: '20px', color: FAIL, whiteSpace: 'pre', opacity: fadeAt(frame, 16.0) * (1 - ramp(frame, 20.6, 21.0)),
            }}
          >
            {'side: "hold"\nexpected one of\n[\'buy\', \'sell\']'}
          </div>
        )}
      </Frame>
      <Frame {...vm} color={ACCENT} title="the program's VM" note="the handlers captured at Launch" opacity={fadeAt(frame, 0.6) * vmDim}>
        <div
          style={{
            position: 'absolute', left: prog.x - vm.x, top: prog.y - vm.y, width: prog.w, height: prog.h, boxSizing: 'border-box', borderRadius: 10,
            border: `2px solid ${ACCENT}`, ...glow(progOn, ACCENT), background: '#0b0d13',
          }}
        >
          <div style={{position: 'absolute', left: 12, top: 6, fontFamily: SANS, fontSize: 14, color: MUTED}}>the tool's program</div>
          <div style={{position: 'absolute', left: 12, top: 34, fontFamily: MONO, fontSize: 17, fontWeight: 700, color: progOn ? LOOK : TEXT, whiteSpace: 'nowrap'}}>{call}</div>
        </div>
        <div
          style={{
            position: 'absolute', left: brk.x - vm.x, top: brk.y - vm.y, width: brk.w, height: brk.h, boxSizing: 'border-box', borderRadius: 10,
            border: `2px solid ${TEST}`, ...glow(brkOn, TEST),
          }}
        >
          <div style={{position: 'absolute', left: 12, top: 6, fontFamily: MONO, fontSize: 18, fontWeight: 700, color: brkOn ? LOOK : TEST}}>paper_broker</div>
          <div style={{position: 'absolute', left: 12, top: 38, fontFamily: SANS, fontSize: 14, color: MUTED}}>fixed prices; keeps a book of positions</div>
        </div>
      </Frame>
      {/* tools/list */}
      <Travel from={[agent.x + agent.w, ay(60)]} to={[server.x, ay(60)]} at={2.2} until={3.2} text="tools/list" color={PROD} />
      <Travel from={[server.x, ay(150)]} to={[agent.x + agent.w, ay(150)]} at={3.4} until={4.2} text="2 tools" color={TEST} />
      {/* query-price-tool: in, the effect out to paper_broker, the answer back out to the agent. */}
      <Travel from={[agent.x + agent.w, ay(60)]} to={[server.x, ay(60)]} at={7.0} until={7.8} text="query-price-tool" color={PROD} />
      <Travel from={[server.x + server.w, ay(110)]} to={[prog.x, prog.y + 40]} at={7.8} until={8.6} text='"7203"' color={PROD} />
      <Travel from={[prog.x + 150, prog.y + prog.h]} to={[brk.x + 150, brk.y]} at={8.8} until={9.6} text="QuotePrice('7203')" color={ACCENT} />
      <Travel from={[brk.x + 300, brk.y]} to={[prog.x + 300, prog.y + prog.h]} at={10.4} until={11.0} text="2480" color={TEST} />
      <Travel from={[prog.x, prog.y + 40]} to={[agent.x + agent.w, ay(230)]} at={11.2} until={12.0} text='"2480"' color={TEST} stay={1.0} />
      {/* side "hold": refused at the server, never reaching the program. */}
      <Travel from={[agent.x + agent.w, ay(60)]} to={[server.x + 60, ay(60)]} at={15.0} until={16.0} text='side: "hold"' color={PROD} />
      <Travel from={[server.x, ay(250)]} to={[agent.x + agent.w, ay(250)]} at={16.8} until={17.6} text="isError" color={FAIL} stay={1.2} />
      {/* side "buy": through to the program and paper_broker. */}
      <Travel from={[agent.x + agent.w, ay(60)]} to={[server.x, ay(60)]} at={22.0} until={22.8} text="submit-order-tool" color={PROD} />
      <Travel from={[server.x + server.w, ay(110)]} to={[prog.x, prog.y + 40]} at={22.8} until={23.4} text='side: "buy"' color={PROD} />
      <Travel from={[prog.x + 150, prog.y + prog.h]} to={[brk.x + 150, brk.y]} at={23.4} until={24.2} text="PlaceOrder(...)" color={ACCENT} />
      <Travel from={[brk.x + 300, brk.y]} to={[prog.x + 300, prog.y + prog.h]} at={24.8} until={25.4} text="filled" color={TEST} />
      <Travel from={[prog.x, prog.y + 40]} to={[agent.x + agent.w, ay(230)]} at={25.4} until={26.0} text='"filled: …"' color={TEST} stay={1.6} />
      <Terminal x={40} y={410} w={1200} lines={CALL_LOG} size={13} line={22} title="measure/agent_flow.log — the agent's side" opacity={fadeAt(frame, 1.8)} />
    </AbsoluteFill>
  );
};

// Scene 5 — same VM, same handlers: what the program sees after Stop, in either handler order.

const SAME_LINES: Lines = [
  [0, "Every tool call ran in the program's VM, under the handlers from Launch."],
  [8, "After Stop, the program asks paper_broker: 100, the agent's shares."],
  [15.5, 'The capture takes handlers on both sides: either order gives 100.'],
];
const SAME = f(24);
// measure/agent_flow.log, in order: the thread each step ran on ([doeff] is the thread run() was called on).
const EVENTS = [
  {ms: '282.6', lane: 0, text: 'Launch'},
  {ms: '503.3', lane: 0, text: 'resumed'},
  {ms: '508.2', lane: 1, text: 'tools/list'},
  {ms: '511.5', lane: 1, text: 'call'},
  {ms: '512.9', lane: 0, text: 'QuotePrice'},
  {ms: '517.2', lane: 1, text: 'call'},
  {ms: '518.2', lane: 0, text: 'PlaceOrder'},
  {ms: '520.8', lane: 0, text: 'Stop'},
  {ms: '1024.3', lane: 0, text: 'Position'},
];

const SameScene: React.FC = () => {
  const frame = useSceneFrame();
  const lane = (i: number) => 150 + i * 90;
  const ex = (i: number) => 260 + i * 112;
  const answered = (i: number) => EVENTS[i].text === 'QuotePrice' || EVENTS[i].text === 'PlaceOrder';
  const orders = [
    {stack: ['claude_agent_handler', 'paper_broker', 'trading_agent'], colors: [PROD, TEST, ACCENT], log: 'agent_flow.log', at: 16.0},
    {stack: ['paper_broker', 'claude_agent_handler', 'trading_agent'], colors: [TEST, PROD, ACCENT], log: 'agent_flow_inner.log', at: 18.2},
  ];
  return (
    <AbsoluteFill>
      <Headlines lines={SAME_LINES} />
      <div style={{position: 'absolute', left: 40, top: 96, width: 1200, height: 230, borderRadius: 14, background: '#0b0d13', border: '2px solid #2a2e40', opacity: fadeAt(frame, 0.2)}} />
      <Label x={60} y={lane(0) - 12} size={17} color={ACCENT} opacity={fadeAt(frame, 0.4)}>[doeff]</Label>
      <Label x={60} y={lane(0) + 10} size={13} bold={false} opacity={fadeAt(frame, 0.4)}>the program's thread</Label>
      <Label x={60} y={lane(1) - 12} size={17} color={PROD} opacity={fadeAt(frame, 0.6)}>[agent]</Label>
      <Label x={60} y={lane(1) + 10} size={13} bold={false} opacity={fadeAt(frame, 0.6)}>HTTP requests</Label>
      {[0, 1].map((i) => (
        <div key={i} style={{position: 'absolute', left: 240, top: lane(i), width: 980, height: 2, background: '#2a2e40', opacity: fadeAt(frame, 0.4)}} />
      ))}
      {EVENTS.map((e, i) => {
        const at = 1.0 + i * 0.6;
        const hot = answered(i) && frame >= f(4.4) && frame < f(8.0);
        const color = e.lane === 0 ? ACCENT : PROD;
        return (
          <React.Fragment key={i}>
            <div
              style={{
                position: 'absolute', left: ex(i) - 9, top: lane(e.lane) - 8, width: 18, height: 18, borderRadius: 9, background: hot ? LOOK : color,
                boxShadow: hot ? `0 0 16px ${LOOK}` : 'none', opacity: fadeAt(frame, at),
              }}
            />
            <div style={{position: 'absolute', left: ex(i) - 59, width: 118, top: lane(e.lane) + 16, textAlign: 'center', opacity: fadeAt(frame, at)}}>
              <div style={{fontFamily: MONO, fontSize: 14, fontWeight: 700, color: hot ? LOOK : TEXT}}>{e.text}</div>
              <div style={{fontFamily: MONO, fontSize: 12, color: MUTED}}>{e.ms} ms</div>
            </div>
          </React.Fragment>
        );
      })}
      {/* Each call from the agent's side, to the effect its tool yielded on the program's thread. */}
      <svg width={1280} height={720} style={{position: 'absolute', left: 0, top: 0, opacity: fadeAt(frame, 4.4) * (1 - ramp(frame, 7.6, 8.0))}}>
        {[[3, 4], [5, 6]].map(([from, to]) => (
          <line key={from} x1={ex(from)} y1={lane(1) - 10} x2={ex(to)} y2={lane(0) + 10} stroke={LOOK} strokeWidth={2} strokeDasharray="6 5" />
        ))}
      </svg>
      <Label x={240} y={110} size={15} color={LOOK} opacity={fadeAt(frame, 4.6) * (1 - ramp(frame, 7.6, 8.0))}>
        paper_broker answers the tools' effects on the program's thread
      </Label>
      {/* After Stop: the program's own question to paper_broker. */}
      <CodeBlock
        lines={['    yield Stop(handle)', '    held = yield Position("7203")', '    return held']} x={40} y={350} w={560} size={17} line={30}
        band={frame >= f(9.0) ? (frame < f(10.0) ? 0 : 1) : null} opacity={fadeAt(frame, 8.2)}
      />
      <Travel from={[560, 420]} to={[560, 540]} at={10.4} until={11.2} text="Position('7203')" color={ACCENT} />
      <Frame x={40} y={510} w={560} h={110} color={TEST} title="paper_broker" note="the book the agent's order changed" on={frame >= f(11.2) && frame < f(12.4)} opacity={fadeAt(frame, 8.4)}>
        <div style={{position: 'absolute', right: 20, top: 30, fontFamily: MONO, fontSize: 34, fontWeight: 700, color: TEXT, opacity: fadeAt(frame, 11.4)}}>7203: 100</div>
      </Frame>
      <Label x={40} y={640} size={18} color={TEST} opacity={fadeAt(frame, 12.4)}>program sees position 100 · run returns 100</Label>
      {/* Either order: the same answer. */}
      {orders.map((o, k) => (
        <React.Fragment key={k}>
          {o.stack.map((name, i) => (
            <div
              key={name}
              style={{
                position: 'absolute', left: 650 + k * 300 + i * 14, top: 360 + i * 56, width: 280 - i * 28, height: 160 - i * 56 + 40 - (i === 2 ? 0 : 0),
                boxSizing: 'border-box', borderRadius: 10, border: `2px solid ${o.colors[i]}`, opacity: fadeAt(frame, o.at + i * 0.2),
              }}
            >
              <div style={{position: 'absolute', left: 10, top: 6, fontFamily: MONO, fontSize: 14, fontWeight: 700, color: o.colors[i], whiteSpace: 'nowrap'}}>{name}</div>
              {i === 2 && (
                <div style={{position: 'absolute', left: 10, top: 32, fontFamily: MONO, fontSize: 12, lineHeight: '18px', color: LOOK, whiteSpace: 'pre', opacity: fadeAt(frame, o.at + 0.8)}}>
                  {`paper_broker is captured by\n${k === 0 ? 'GetHandlers(k)' : 'GetOuterHandlers()'}`}
                </div>
              )}
            </div>
          ))}
          <div style={{position: 'absolute', left: 650 + k * 300, top: 580, width: 280, textAlign: 'center', fontFamily: MONO, fontSize: 18, fontWeight: 700, color: TEST, opacity: fadeAt(frame, o.at + 1.2)}}>
            run returns 100
          </div>
          <div style={{position: 'absolute', left: 650 + k * 300, top: 610, width: 280, textAlign: 'center', fontFamily: MONO, fontSize: 12, color: MUTED, opacity: fadeAt(frame, o.at + 1.2)}}>
            measure/{o.log}
          </div>
        </React.Fragment>
      ))}
    </AbsoluteFill>
  );
};

// Outro — the chapter in one sentence.

const OUTRO = f(10);

const Outro: React.FC = () => {
  const frame = useSceneFrame();
  const rise = interpolate(frame, [0, f(0.67)], [20, 0], {extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic)});
  const steps = [
    {text: 'defmcp-tool', color: ACCENT},
    {text: 'Launch(mcp_tools=...)', color: ACCENT},
    {text: 'tools/call', color: PROD},
    {text: 'your handlers answer', color: TEST},
  ];
  return (
    <AbsoluteFill style={{padding: 90, justifyContent: 'center', transform: `translateY(${rise}px)`}}>
      <div style={{fontFamily: SANS, fontSize: 56, fontWeight: 700, color: TEXT, opacity: fadeAt(frame, 0, 0.4)}}>MCP Tools</div>
      <div style={{fontFamily: SANS, fontSize: 30, color: ACCENT, marginTop: 6, opacity: fadeAt(frame, 0.2, 0.4)}}>Your defk is the agent's tool; your handlers give its effects their meaning</div>
      <div style={{display: 'flex', alignItems: 'center', marginTop: 50, gap: 18}}>
        {steps.map((s, i) => (
          <React.Fragment key={s.text}>
            {i > 0 && <div style={{fontFamily: SANS, fontSize: 30, color: MUTED, opacity: fadeAt(frame, 0.8 + i * 0.9)}}>→</div>}
            <div
              style={{
                fontFamily: MONO, fontSize: 22, fontWeight: 700, color: s.color, padding: '10px 16px', borderRadius: 12, border: `2px solid ${s.color}`,
                background: PANEL, whiteSpace: 'nowrap', opacity: fadeAt(frame, 0.8 + i * 0.9),
              }}
            >
              {s.text}
            </div>
          </React.Fragment>
        ))}
      </div>
      <div style={{fontFamily: MONO, fontSize: 24, color: TEST, marginTop: 50, opacity: fadeAt(frame, 4.6, 0.4)}}>doeff-agents</div>
    </AbsoluteFill>
  );
};

// The scenes in order; Root.tsx registers MCP_TOOLS.length frames of MCP_TOOLS.Video.
export const MCP_TOOLS = narrate('mcp-tools', DURATIONS, [
  {length: IDEA, starts: startsOf(IDEA_LINES), ids: ['t1', 't2', 't3'], node: <IdeaScene />},
  {length: DEFINE, starts: startsOf(DEFINE_LINES), ids: ['d1', 'd2', 'd3'], node: <DefineScene />},
  {length: LAUNCH, starts: startsOf(LAUNCH_LINES), ids: ['l1', 'l2', 'l3', 'l4', 'l5'], node: <LaunchScene />},
  {length: CALL, starts: startsOf(CALL_LINES), ids: ['c1', 'c2', 'c3', 'c4'], node: <CallScene />},
  {length: SAME, starts: startsOf(SAME_LINES), ids: ['s1', 's2', 's3'], node: <SameScene />},
  {length: OUTRO, starts: [0], ids: ['z1'], node: <Outro />},
]);
