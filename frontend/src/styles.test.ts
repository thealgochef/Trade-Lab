import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

// COCKPIT-FIX F1: jsdom performs no layout, so the layout decoupling is pinned
// as a stylesheet contract — the chart wrapper's height must be a function of
// the viewport alone, and nothing may re-couple the two workspace columns.

const rawStyles = readFileSync(resolve(process.cwd(), 'src/styles.css'), 'utf8');
const stylesheet = rawStyles.replace(/\/\*[\s\S]*?\*\//g, '');

const ruleFor = (selector: string): string => {
  const start = stylesheet.indexOf(`${selector} {`);
  if (start === -1) throw new Error(`no rule found for ${selector}`);
  const end = stylesheet.indexOf('}', start);
  return stylesheet.slice(start + selector.length + 2, end);
};

describe('workspace layout contract (COCKPIT-FIX F1)', () => {
  it('declares the shared workspace height from viewport units only', () => {
    const declaration = ruleFor(':root').match(/--workspace-h:[^;]*/)?.[0] ?? '';
    expect(declaration).toContain('vh');
    expect(declaration).not.toContain('%');
    expect(declaration).not.toContain('auto');
  });

  it('sizes the chart wrapper from the viewport variable, never from content or siblings', () => {
    const rule = ruleFor('.chart-shell');
    expect(rule).toMatch(/height:\s*var\(--workspace-h\)/);
    expect(rule).not.toMatch(/height:\s*100%/);
  });

  it('keeps the workspace columns from stretching to each other', () => {
    expect(ruleFor('.workspace-grid')).toMatch(/align-items:\s*start/);
  });

  it('bounds the sidebar to the workspace height with internal scrolling', () => {
    const rule = ruleFor('.intelligence-panel');
    expect(rule).toMatch(/max-height:\s*calc\(var\(--workspace-h\)/);
    expect(rule).toMatch(/overflow-y:\s*auto/);
  });

  it('bounds each sidebar section with its own internal scroll', () => {
    const body = ruleFor('.intel-section-body');
    expect(body).toMatch(/max-height:\s*\d+px/);
    expect(body).toMatch(/overflow-y:\s*auto/);
    expect(ruleFor('.intel-section-body.predictions')).toMatch(/max-height:\s*\d+px/);
  });
});
