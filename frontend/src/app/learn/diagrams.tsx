/* Simple explanatory SVG diagrams (not market data). */
const G = "#1a9b48", R = "#e0342b", T = "#6e6e73", B = "#0071e3", A = "#d97706";

function Frame({ children, h = 170 }: { children: React.ReactNode; h?: number }) {
  return <svg viewBox={`0 0 520 ${h}`} className="my-3 w-full max-w-[560px] rounded border border-line bg-panel2">{children}</svg>;
}
const Txt = ({ x, y, c = T, s = 11, children, a = "start" }: any) => <text x={x} y={y} fill={c} fontSize={s} textAnchor={a}>{children}</text>;
function Candle({ x, o, c, h, l, w = 14 }: { x: number; o: number; c: number; h: number; l: number; w?: number }) {
  const col = c < o ? G : R; // svg y grows downward: c<o means close above open
  return (<g><line x1={x} x2={x} y1={h} y2={l} stroke={col} strokeWidth={1.5} /><rect x={x - w / 2} y={Math.min(o, c)} width={w} height={Math.max(2, Math.abs(c - o))} fill={col} /></g>);
}

export function Diagram({ name }: { name: string }) {
  switch (name) {
    case "candle":
      return (<Frame><Candle x={150} o={120} c={60} h={30} l={145} w={34} /><Candle x={360} o={60} c={120} h={30} l={145} w={34} />
        {[["High", 30], ["Close", 60], ["Open", 120], ["Low", 145]].map(([t, y]) => <g key={t as string}><line x1={175} x2={215} y1={y as number} y2={y as number} stroke="#d2d2d7" strokeDasharray="3 3" /><Txt x={220} y={(y as number) + 4}>{t}</Txt></g>)}
        <Txt x={150} y={165} a="middle" c={G}>bullish (close &gt; open)</Txt><Txt x={360} y={165} a="middle" c={R}>bearish (close &lt; open)</Txt>
        <Txt x={385} y={40}>upper wick</Txt><Txt x={385} y={95}>body</Txt><Txt x={385} y={140}>lower wick</Txt></Frame>);
    case "structure":
      return (<Frame h={190}><polyline points="20,160 90,100 130,130 210,60 250,95 330,30 370,70 420,90 460,140 500,170" fill="none" stroke={B} strokeWidth={2} />
        {[[90, 100, "HH"], [130, 130, "HL"], [210, 60, "HH"], [250, 95, "HL"], [330, 30, "HH"], [370, 70, "HL"]].map(([x, y, l]) => <Txt key={`${x}`} x={x} y={(y as number) - 6} a="middle" c={G}>{l}</Txt>)}
        <line x1={90} x2={200} y1={100} y2={100} stroke={G} strokeDasharray="4 3" /><Txt x={150} y={95} c={G} a="middle">BOS</Txt>
        <line x1={370} x2={445} y1={70} y2={70} stroke="transparent" /><line x1={250} x2={460} y1={95} y2={95} stroke={R} strokeDasharray="4 3" /><Txt x={470} y={92} c={R}>CHoCH</Txt>
        <Txt x={20} y={182}>Uptrend: higher highs + higher lows. Breaking the last higher low = Change of Character.</Txt></Frame>);
    case "sweep":
      return (<Frame><line x1={20} x2={500} y1={60} y2={60} stroke={A} strokeDasharray="5 4" /><Txt x={22} y={52} c={A}>equal highs → buy-side liquidity (stops)</Txt>
        {[60, 100, 140, 180, 220, 260].map((x, i) => <Candle key={x} x={x} o={110 - (i % 2) * 20} c={90 + (i % 2) * 15} h={62 + (i % 3)} l={130} />)}
        <Candle x={310} o={95} c={80} h={35} l={100} /><Txt x={505} y={30} c={A} a="end">wick through, close back below = sweep</Txt>
        <Candle x={350} o={82} c={135} h={78} l={140} /><Candle x={390} o={132} c={150} h={125} l={158} /><Txt x={505} y={165} a="end">strong reversal = stop-hunt candidate</Txt></Frame>);
    case "ob":
      return (<Frame><rect x={150} y={110} width={360} height={28} fill="#22c55e22" stroke={G} strokeDasharray="4 3" /><Txt x={360} y={128} c={G}>bullish order block zone</Txt>
        <Candle x={60} o={70} c={95} h={65} l={100} /><Candle x={100} o={95} c={120} h={90} l={128} /><Candle x={150} o={113} c={135} h={110} l={138} />
        <Candle x={190} o={135} c={70} h={65} l={138} /><Candle x={230} o={70} c={40} h={30} l={75} />
        <line x1={20} x2={260} y1={62} y2={62} stroke={B} strokeDasharray="4 3" /><Txt x={265} y={66} c={B}>prior swing high → broken (BOS)</Txt>
        <Candle x={320} o={45} c={90} h={40} l={95} /><Candle x={360} o={90} c={120} h={85} l={128} /><Candle x={400} o={122} c={80} h={75} l={126} />
        <Txt x={150} y={158} a="middle">last red candle before the displacement</Txt><Txt x={380} y={160}>price returns → possible reaction</Txt></Frame>);
    case "fvg":
      return (<Frame><Candle x={140} o={130} c={110} h={100} l={140} w={30} /><Candle x={200} o={110} c={40} h={35} l={115} w={30} /><Candle x={260} o={50} c={30} h={20} l={70} w={30} />
        <rect x={120} y={70} width={380} height={30} fill="#10b98133" /><Txt x={300} y={90} c={G}>gap: candle 3 low &gt; candle 1 high</Txt>
        <line x1={120} x2={500} y1={100} y2={100} stroke={G} strokeDasharray="3 3" /><line x1={120} x2={500} y1={70} y2={70} stroke={G} strokeDasharray="3 3" />
        <Txt x={140} y={160} a="middle">1</Txt><Txt x={200} y={160} a="middle">2</Txt><Txt x={260} y={160} a="middle">3</Txt></Frame>);
    case "pd":
      return (<Frame><rect x={60} y={20} width={300} height={65} fill="#ef444418" /><rect x={60} y={85} width={300} height={65} fill="#22c55e18" />
        <rect x={60} y={111} width={300} height={22} fill="#a855f730" /><line x1={60} x2={360} y1={85} y2={85} stroke={T} strokeDasharray="4 3" />
        <polyline points="70,150 200,20 300,120" fill="none" stroke={B} strokeWidth={2} />
        <Txt x={370} y={55} c={R}>Premium (expensive) — sell area</Txt><Txt x={370} y={89}>Equilibrium 50%</Txt><Txt x={370} y={126} c="#7c3aed">OTE 62–79%</Txt><Txt x={370} y={146} c={G}>Discount (cheap) — buy area</Txt></Frame>);
    case "elliott":
      return (<Frame h={180}><polyline points="20,160 80,100 110,130 210,40 240,75 300,20 340,80 370,55 420,120" fill="none" stroke={B} strokeWidth={2} />
        {[[80, 100, "1"], [110, 130, "2"], [210, 40, "3"], [240, 75, "4"], [300, 20, "5"], [340, 80, "A"], [370, 55, "B"], [420, 120, "C"]].map(([x, y, l]) => (
          <g key={l as string}><circle cx={x as number} cy={y as number} r={9} fill={B} /><Txt x={x} y={(y as number) + 4} a="middle" c="#ffffff" s={10}>{l}</Txt></g>))}
        <line x1={80} x2={300} y1={100} y2={100} stroke={R} strokeDasharray="3 3" /><Txt x={440} y={104} c={R}>wave 4 must stay above wave 1 top</Txt>
        <Txt x={20} y={175}>Impulse 1-2-3-4-5 with the trend, then correction A-B-C.</Txt></Frame>);
    case "rr":
      return (<Frame><rect x={120} y={30} width={260} height={60} fill="#22c55e26" /><rect x={120} y={90} width={260} height={30} fill="#ef444426" />
        <line x1={120} x2={380} y1={90} y2={90} stroke="#3a3a3c" /><Txt x={390} y={34} c={G}>Target +2R ($20)</Txt><Txt x={390} y={94}>Entry</Txt><Txt x={390} y={124} c={R}>Stop −1R ($10)</Txt>
        <Txt x={20} y={150}>$1,000 account × 1% risk = $10 at risk. Size = $10 ÷ (entry − stop).</Txt></Frame>);
    default:
      return null;
  }
}
