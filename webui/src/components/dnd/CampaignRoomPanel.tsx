import { useCallback, useEffect, useState } from "react";
import { LogIn, Users } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useClient } from "@/providers/ClientProvider";
import type { DndCampaignInfo, DndRoomCharacter } from "@/lib/types";
import { fetchDndCampaigns, fetchDndRoom } from "@/lib/api";

interface Props {
  onJoin: (campaignId: string, characterId: string, characterName: string) => void;
}

export function CampaignRoomPanel({ onJoin }: Props) {
  const { token } = useClient();
  const [campaigns, setCampaigns] = useState<DndCampaignInfo[]>([]);
  const [selectedCampaign, setSelectedCampaign] = useState<string | null>(null);
  const [characters, setCharacters] = useState<DndRoomCharacter[]>([]);
  const [chosenChar, setChosenChar] = useState<string>("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const list = await fetchDndCampaigns(token, "", "active");
      setCampaigns(list);
    } finally { setLoading(false); }
  }, [token]);

  useEffect(() => { load(); }, [load]);

  const handleSelectCampaign = async (cid: string) => {
    setSelectedCampaign(cid);
    setChosenChar("");
    try {
      const room = await fetchDndRoom(token, cid);
      setCharacters(room.characters);
    } catch {
      setCharacters([]);
    }
  };

  const handleJoin = () => {
    if (!selectedCampaign || !chosenChar) return;
    const char = characters.find((c) => c.id === chosenChar);
    if (char) onJoin(selectedCampaign, char.id, char.name);
  };

  if (loading) return <div className="text-muted-foreground text-sm p-4">Loading...</div>;

  return (
    <div className="space-y-4">
      {!selectedCampaign ? (
        <div className="space-y-2">
          <h2 className="text-lg font-semibold flex items-center gap-2"><Users className="w-5 h-5" />Join Campaign</h2>
          {!campaigns.length && <p className="text-muted-foreground text-sm">No active campaigns</p>}
          {campaigns.map((c) => (
            <button
              key={c.id}
              onClick={() => handleSelectCampaign(c.id)}
              className="w-full flex items-center justify-between px-4 py-3 border border-border rounded-lg hover:bg-muted/50 text-left"
            >
              <div>
                <span className="font-medium">{c.name}</span>
                <span className="ml-2 text-xs text-muted-foreground">{c.module_name || ""}</span>
              </div>
              <span className="text-xs text-muted-foreground">{c.save_count} saves</span>
            </button>
          ))}
        </div>
      ) : (
        <div className="space-y-3">
          <button className="text-sm text-muted-foreground hover:text-foreground" onClick={() => setSelectedCampaign(null)}>
            ← Back to campaigns
          </button>
          <h3 className="font-medium">Choose your character</h3>
          {!characters.length && <p className="text-muted-foreground text-sm">No PCs bound to this campaign. Create one first.</p>}
          {characters.map((ch) => (
            <button
              key={ch.id}
              onClick={() => setChosenChar(ch.id)}
              className={`w-full flex items-center justify-between px-4 py-3 border rounded-lg text-left transition-colors ${
                chosenChar === ch.id ? "border-primary bg-primary/10" : "border-border hover:bg-muted/50"
              }`}
            >
              <div>
                <span className="font-medium">{ch.name}</span>
                <span className="ml-2 text-xs text-muted-foreground">{ch.class_name} Lv{ch.level}</span>
              </div>
              {ch.player_name && <span className="text-xs text-muted-foreground">{ch.player_name}</span>}
            </button>
          ))}
          <Button onClick={handleJoin} disabled={!chosenChar} className="w-full">
            <LogIn className="w-4 h-4 mr-2" />Join as {characters.find((c) => c.id === chosenChar)?.name || "..."}
          </Button>
        </div>
      )}
    </div>
  );
}
