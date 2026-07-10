import { describe, expect, it, vi } from 'vitest';
import type { UTCTimestamp } from 'lightweight-charts';
import { SessionShadingPrimitive, bandRectsFor } from './sessionShading';
import type { SessionBand } from './viewModels';

const band = (session: SessionBand['session'], from: number, to: number): SessionBand => ({
  session,
  from: from as UTCTimestamp,
  to: to as UTCTimestamp,
});

describe('bandRectsFor', () => {
  const linear = (time: UTCTimestamp) => (time as number) - 1000; // 1 chart-second = 1px, origin at t=1000

  it('converts visible bands to x extents and skips off-screen bands', () => {
    const rects = bandRectsFor(
      [band('ny', 1010, 1050), band('asia', 2000, 2100)],
      { from: 1000, to: 1100 },
      linear,
      100,
    );
    expect(rects).toEqual([{ x: 10, width: 40, session: 'ny' }]);
  });

  it('clamps bands that extend past the visible range to the pane edges', () => {
    const rects = bandRectsFor(
      [band('london', 900, 1050), band('ny', 1080, 1300)],
      { from: 1000, to: 1100 },
      linear,
      100,
    );
    expect(rects[0]).toEqual({ x: 0, width: 50, session: 'london' });
    expect(rects[1]).toEqual({ x: 80, width: 20, session: 'ny' });
  });

  it('renders nothing without a visible range or pane width', () => {
    expect(bandRectsFor([band('ny', 0, 10)], null, linear, 100)).toEqual([]);
    expect(bandRectsFor([band('ny', 0, 10)], { from: 0, to: 10 }, linear, 0)).toEqual([]);
  });

  it('gives single-bar bands a minimum 1px width', () => {
    const rects = bandRectsFor([band('ny', 1050, 1050)], { from: 1000, to: 1100 }, linear, 100);
    expect(rects).toEqual([{ x: 50, width: 1, session: 'ny' }]);
  });
});

describe('SessionShadingPrimitive', () => {
  it('draws clamped translucent rects behind the pane via drawBackground at z-order bottom', () => {
    const primitive = new SessionShadingPrimitive();
    const requestUpdate = vi.fn();
    const timeScale = {
      getVisibleRange: () => ({ from: 1000, to: 1100 }),
      timeToCoordinate: (time: unknown) => (time as number) - 1000,
      width: () => 100,
    };
    primitive.attached({ chart: { timeScale: () => timeScale }, requestUpdate } as never);

    primitive.setBands([band('ny', 1010, 1050)]);
    expect(requestUpdate).toHaveBeenCalledOnce();

    const view = primitive.paneViews()[0];
    expect(view.zOrder()).toBe('bottom');

    const fillRect = vi.fn();
    let fillStyle = '';
    const context = new Proxy({} as CanvasRenderingContext2D, {
      set: (_target, prop, value) => {
        if (prop === 'fillStyle') fillStyle = value as string;
        return true;
      },
      get: (_target, prop) => (prop === 'fillRect' ? fillRect : undefined),
    });
    const target = {
      useBitmapCoordinateSpace: (handler: (scope: unknown) => void) =>
        handler({ context, bitmapSize: { width: 200, height: 400 }, horizontalPixelRatio: 2, verticalPixelRatio: 2 }),
    };
    view.renderer().drawBackground(target as never);

    expect(fillRect).toHaveBeenCalledWith(20, 0, 80, 400);
    expect(fillStyle).toContain('rgba(54, 211, 153');

    primitive.detached();
    fillRect.mockClear();
    view.renderer().drawBackground(target as never);
    expect(fillRect).not.toHaveBeenCalled();
  });
});
