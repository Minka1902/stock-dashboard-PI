import { useCallback, useEffect, useState } from "react";
import * as api from "../api";

/**
 * Owns the multi-watchlist UI state for the Watchlist view (Task 13): the set
 * of named lists, which one is active, and that list's items. All mutations go
 * through the API and reflect its returned payload, so the UI never drifts.
 */
export function useWatchlists() {
  const [lists, setLists] = useState([]);
  const [activeId, setActiveId] = useState(null);
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    api.getWatchlists()
      .then((ls) => {
        if (!alive) return;
        setLists(ls);
        setActiveId((cur) => cur ?? ls[0]?.id ?? null);
      })
      .catch(() => {})
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (activeId == null) { setItems([]); return undefined; }
    let alive = true;
    api.getWatchlist(activeId).then((its) => { if (alive) setItems(its); }).catch(() => {});
    return () => { alive = false; };
  }, [activeId]);

  const addTicker = useCallback(async (ticker, note) => {
    setItems(await api.addWatch(ticker, note, activeId));
  }, [activeId]);

  const removeTicker = useCallback(async (ticker) => {
    setItems(await api.removeWatch(ticker, activeId));
  }, [activeId]);

  // Move a ticker to another list. The API returns the *source* list's
  // remaining items, which is exactly what this view is showing.
  const moveTicker = useCallback(async (ticker, toListId) => {
    setItems(await api.updateWatch(ticker, { fromListId: activeId, toListId }));
  }, [activeId]);

  const editNote = useCallback(async (ticker, note) => {
    setItems(await api.updateWatch(ticker, { fromListId: activeId, note }));
  }, [activeId]);

  const createList = useCallback(async (name) => {
    const ls = await api.createWatchlist(name);
    setLists(ls);
    const created = ls[ls.length - 1]; // newest by sort_order
    if (created) setActiveId(created.id);
  }, []);

  const renameList = useCallback(async (id, name) => {
    setLists(await api.renameWatchlist(id, name));
  }, []);

  const deleteList = useCallback(async (id) => {
    const ls = await api.deleteWatchlist(id);
    setLists(ls);
    setActiveId((cur) => (cur === id ? (ls[0]?.id ?? null) : cur));
  }, []);

  return {
    lists, activeId, items, loading,
    selectList: setActiveId, addTicker, removeTicker, moveTicker, editNote,
    createList, renameList, deleteList,
  };
}
