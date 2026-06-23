import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Plus, Trash2, Archive, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { DndCampaignInfo } from "@/lib/types";
import {
  fetchDndCampaigns,
  fetchDndCampaign,
  deleteDndCampaign,
  setDndCampaignStatus,
} from "@/lib/api";
import { cn } from "@/lib/utils";

interface Props {
  token: string;
}

export function CampaignPanel({ token }: Props) {
  const { t } = useTranslation();
  const [campaigns, setCampaigns] = useState<DndCampaignInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [detail, setDetail] = useState<DndCampaignInfo | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setCampaigns(await fetchDndCampaigns(token));
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => { load(); }, [load]);

  const handleDelete = async (id: string) => {
    await deleteDndCampaign(token, id);
    if (expanded === id) setExpanded(null);
    load();
  };

  const handleToggleStatus = async (id: string, current: string) => {
    const next = current === "active" ? "archived" : "active";
    await setDndCampaignStatus(token, id, next);
    if (expanded === id) setDetail(await fetchDndCampaign(token, id));
    load();
  };

  if (loading) return <div className="text-muted-foreground text-sm p-4">Loading...</div>;
  if (!campaigns.length) {
    return (
      <div className="text-center text-muted-foreground py-12">
        <p className="mb-2">{t("dnd.noCampaigns", "No campaigns yet")}</p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {campaigns.map((c) => (
        <div key={c.id} className="border border-border rounded-lg overflow-hidden">
          <button
            className="w-full flex items-center justify-between px-4 py-3 hover:bg-muted/50 text-left"
            onClick={async () => {
              if (expanded === c.id) { setExpanded(null); return; }
              setExpanded(c.id);
              setDetail(await fetchDndCampaign(token, c.id));
            }}
          >
            <div>
              <span className="font-medium">{c.name}</span>
              <span className={cn(
                "ml-2 text-xs px-1.5 py-0.5 rounded",
                c.status === "active"
                  ? "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300"
                  : "bg-amber-100 text-amber-700 dark:bg-amber-900 dark:text-amber-300",
              )}>
                {c.status}
              </span>
            </div>
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <span>{c.module_name || "—"}</span>
              <span>·</span>
              <span>{t("dnd.saves", "Saves")}: {c.save_count}</span>
              {c.status === "active" && (
                <a
                  href={`#/room/${c.id}`}
                  className="ml-2 px-2 py-0.5 rounded bg-primary/10 text-primary hover:bg-primary/20 text-xs font-medium"
                  onClick={(e) => e.stopPropagation()}
                >
                  Join Room
                </a>
              )}
            </div>
          </button>
          {expanded === c.id && detail && (
            <div className="border-t border-border px-4 py-3 space-y-2 text-sm bg-muted/20">
              {detail.rule_set && (
                <p>
                  <span className="text-muted-foreground">Rules: </span>
                  {detail.rule_set.game_system} {detail.rule_set.edition} / {detail.rule_set.release} [{detail.rule_set.locale}]
                </p>
              )}
              {detail.publications && detail.publications.length > 0 && (
                <div>
                  <span className="text-muted-foreground">Publications: </span>
                  {detail.publications.map((p) => (
                    <span key={p.id} className="mr-2 text-xs px-1.5 py-0.5 rounded bg-muted">
                      {p.name} [{p.type}]
                    </span>
                  ))}
                </div>
              )}
              {detail.description && (
                <p className="text-muted-foreground">{detail.description}</p>
              )}
              <div className="flex gap-2 pt-1">
                <Button size="sm" variant="outline" onClick={() => handleToggleStatus(c.id, c.status)}>
                  {c.status === "active" ? <Archive className="w-3.5 h-3.5 mr-1" /> : <RotateCcw className="w-3.5 h-3.5 mr-1" />}
                  {c.status === "active" ? "Archive" : "Reactivate"}
                </Button>
                <Button size="sm" variant="outline" className="text-destructive" onClick={() => handleDelete(c.id)}>
                  <Trash2 className="w-3.5 h-3.5 mr-1" />Delete
                </Button>
              </div>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
