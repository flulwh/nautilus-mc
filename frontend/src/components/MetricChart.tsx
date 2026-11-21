import { useElementSize } from "@mantine/hooks";
import { useEffect, useRef } from "react";
import uPlot from "uplot";

export interface Series {
  label: string;
  color: string;
  unit?: string;
  data: number[];
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
  const hostRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<uPlot | null>(null);

  useEffect(() => {
    if (!hostRef.current || width < 80) return;
    const data: uPlot.AlignedData = [timestamps, ...series.map((s) => s.data)] as uPlot.AlignedData;

    // Within a short window uPlot splits on seconds; dropping them collapsed
    // every label to the same "HH:MM".
    const span = timestamps.length > 1 ? timestamps[timestamps.length - 1] - timestamps[0] : 0;
    const withSeconds = span > 0 && span <= 900;

    const opts: uPlot.Options = {
      width,
      height,
      padding: [10, 8, 0, 4],
      cursor: { show: true, drag: { x: false, y: false }, points: { size: 5 } },
      legend: { show: false },
      scales: { x: { time: true }, y: { range: (_u, _min, max) => [min, max <= 0 ? 10 : max * 1.15] } },
      axes: [
        {
          stroke: "#5d7183",
          grid: { stroke: "#16222e", width: 1 },
          ticks: { stroke: "#16222e", width: 1 },
          font: "10px Inter, sans-serif",
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
          stroke: "#5d7183",
          grid: { stroke: "#16222e", width: 1 },
          ticks: { stroke: "#16222e", width: 1 },
          font: "10px Inter, sans-serif",
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
        })),
      ],
    };

    const chart = new uPlot(opts, data, hostRef.current);
    chartRef.current = chart;
    return () => {
      chart.destroy();
      chartRef.current = null;
    };
  }, [width, height, min, series, timestamps]);

  return (
    <div ref={sizeRef} style={{ width: "100%" }}>
      <div ref={hostRef} />
    </div>
  );
}
