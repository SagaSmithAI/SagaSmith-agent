import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "@/providers/ClientProvider";
import { CampaignPanel } from "./CampaignPanel";
import { CharacterPanel } from "./CharacterPanel";
import { WorldPanel } from "./WorldPanel";
import { cn } from "@/lib/utils";

type DndTab = "campaigns" | "characters" | "world";

export function DndDashboard({ className }: { className?: string }) {
  const { t } = useTranslation();
  const { token } = useClient();
  const [tab, setTab] = useState<DndTab>("campaigns");

  const tabs: { key: DndTab; label: string }[] = [
    { key: "campaigns", label: t("dnd.campaigns", "Campaigns") },
    { key: "characters", label: t("dnd.characters", "Characters") },
    { key: "world", label: t("dnd.world", "World") },
  ];

  return (
    <div className={cn("flex flex-col h-full bg-background", className)}>
      <div className="flex items-center gap-1 px-4 pt-4 pb-2 border-b border-border">
        <h1 className="text-lg font-semibold mr-4">D&D</h1>
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={cn(
              "px-3 py-1.5 text-sm rounded-md transition-colors",
              tab === t.key
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:text-foreground hover:bg-muted",
            )}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className="flex-1 overflow-auto p-4">
        {tab === "campaigns" && <CampaignPanel token={token} />}
        {tab === "characters" && <CharacterPanel token={token} />}
        {tab === "world" && <WorldPanel token={token} />}
      </div>
    </div>
  );
}
