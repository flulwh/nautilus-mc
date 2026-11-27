import { useMantineColorScheme } from "@mantine/core";
import { useElementSize } from "@mantine/hooks";
import { useEffect, useMemo, useRef } from "react";
import uPlot from "uplot";

export interface Series {
  label: string;
  color: string;
  unit?: string;
  data: (number | null)[];
}

/** Thin uPlot wrapper: 60KB, canvas based, handles ~10k points without dropping frames. */
export function MetricChart({
  timestamps,
  series,
  height = 180,
  min = 0,
}: {
  timestamps: number[];
  series: Series[];
  height?: number;
  min?: number;
}) {
  const { ref: sizeRef, width } = useElementSize();
  const { colorScheme } = useMantineColorScheme();
  // Canvas can't read CSS variables, so mirror the Codex neutral tokens here.
  const axisStroke = colorScheme === "dark" ? "#8f8c82" : "#7a7568";
  const gridStroke = colorScheme === "dark" ? "#2a2b31" : "#e4e0d6";
  const hostRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<uPlot | null>(null);
  // Draw-in progress: the line grows from the left instead of appearing whole.
  const growRef = useRef(0);
  const growRaf = useRef(0);

  // Within a short window uPlot splits on seconds; dropping them collapsed
  // every label to the same "HH:MM".
  const span = timestamps.length > 1 ? timestamps[timestamps.length - 1] - timestamps[0] : 0;
  const withSeconds = span > 0 && span <= 900;

  const data = useMemo<uPlot.AlignedData>(
    () => [timestamps, ...series.map((s) => s.data)] as uPlot.AlignedData,
    [timestamps, series],
  );

  // Structure only: the chart is rebuilt when the geometry changes, not on
  // every sample. Recreating it per tick (every 3s) destroyed and rebuilt
  // three canvases just to move one point.
  useEffect(() => {
    if (!hostRef.current || width < 80) return;
    // Wrap the default linear path builder so the curve grows from the left
    // instead of appearing whole.
    const linearPaths = uPlot.paths.linear?.();

    const opts: uPlot.Options = {
      width,
      height,
      padding: [10, 8, 0, 4],
      cursor: { show: true, drag: { x: false, y: false }, points: { size: 5 } },
      legend: { show: false },
      scales: { x: { time: true }, y: { range: (_u, _min, max) => [min, max <= 0 ? 10 : max * 1.15] } },
      axes: [
        {
          stroke: axisStroke,
          grid: { stroke: gridStroke, width: 1 },
          ticks: { stroke: gridStroke, width: 1 },
          font: "10px ui-monospace, Cascadia Mono, Consolas, monospace",
          // uPlot's default HH:MM:SS labels need ~52px; with 28px they were
          // silently truncated to "-50".
          size: withSeconds ? 52 : 40,
          values: (_u, splits) =>
            splits.map((v) => {
              const d = new Date(v * 1000);
              const hh = String(d.getHours()).padStart(2, "0");
              const mm = String(d.getMinutes()).padStart(2, "0");
              return withSeconds ? `${hh}:${mm}:${String(d.getSeconds()).padStart(2, "0")}` : `${hh}:${mm}`;
            }),
        },
        {
          stroke: axisStroke,
          grid: { stroke: gridStroke, width: 1 },
          ticks: { stroke: gridStroke, width: 1 },
          font: "10px ui-monospace, Cascadia Mono, Consolas, monospace",
          size: 40,
        },
      ],
      series: [
        {},
        ...series.map((s) => ({
          label: s.label,
          stroke: s.color,
          width: 1.6,
          fill: `${s.color}18`,
          points: { show: false },
          paths: linearPaths
            ? (self: uPlot, seriesIdx: number, idx0: number, idx1: number) => {
                const frac = growRef.current;
                if (frac >= 1) return linearPaths(self, seriesIdx, idx0, idx1);
                const spanIdx = idx1 - idx0;
                const cut = Math.max(idx0, Math.round(idx0 + spanIdx * frac));
                return linearPaths(self, seriesIdx, idx0, cut);
              }
            : undefined,
        })),
      ],
    };

    const chart = new uPlot(opts, data, hostRef.current);
    chartRef.current = chart;

    // Grow the curves in once when the chart is (re)built.
    growRef.current = 0;
    const started = performance.now();
    const tick = (now: number) => {
      const t = Math.min(1, (now - started) / 520);
      // Ease-out cubic: fast start, gentle settle.
      growRef.current = 1 - Math.pow(1 - t, 3);
      chart.redraw(false, false);
      if (t < 1) {
        growRaf.current = requestAnimationFrame(tick);
      }
    };
    growRaf.current = requestAnimationFrame(tick);

    return () => {
      cancelAnimationFrame(growRaf.current);
      chart.destroy();
      chartRef.current = null;
    };
    // ``data`` and ``series`` are intentionally excluded: they are applied by
    // the update effect below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [width, height, min, withSeconds, colorScheme]);

  useEffect(() => {
    chartRef.current?.setData(data);
  }, [data]);

  return (
    <div ref={sizeRef} style={{ width: "100%" }}>
      <div ref={hostRef} />
    </div>
  );
}
