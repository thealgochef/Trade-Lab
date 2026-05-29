import { useSyncExternalStore } from 'react';

export type Store<T> = {
  getSnapshot: () => T;
  setState: (updater: Partial<T> | ((current: T) => T)) => void;
  subscribe: (listener: () => void) => () => void;
  reset: () => void;
};

export const createStore = <T>(initialState: T): Store<T> => {
  let state = initialState;
  const listeners = new Set<() => void>();
  return {
    getSnapshot: () => state,
    setState: (updater) => {
      state = typeof updater === 'function' ? updater(state) : { ...state, ...updater };
      listeners.forEach((listener) => listener());
    },
    subscribe: (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    reset: () => {
      state = initialState;
      listeners.forEach((listener) => listener());
    },
  };
};

export function useStore<T>(store: Store<T>): T;
export function useStore<T, U>(store: Store<T>, selector: (state: T) => U): U;
export function useStore<T, U>(store: Store<T>, selector?: (state: T) => U): T | U {
  return useSyncExternalStore(
    store.subscribe,
    () => (selector ? selector(store.getSnapshot()) : store.getSnapshot()),
    () => (selector ? selector(store.getSnapshot()) : store.getSnapshot()),
  );
}
