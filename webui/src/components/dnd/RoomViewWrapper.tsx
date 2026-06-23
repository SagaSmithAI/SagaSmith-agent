import { useState } from "react";
import { CampaignRoomPanel } from "./CampaignRoomPanel";
import { RoomView } from "./RoomView";

interface Props {
  campaignId: string;
  onNavigateHome: () => void;
}

export function RoomViewWrapper({ campaignId, onNavigateHome }: Props) {
  const [charId, setCharId] = useState<string | null>(null);
  const [charName, setCharName] = useState<string>("");

  if (!charId || !charName) {
    return (
      <div className="flex-1 overflow-auto p-4">
        <CampaignRoomPanel
          onJoin={(cid, id, name) => {
            setCharId(id);
            setCharName(name);
          }}
        />
      </div>
    );
  }

  return (
    <RoomView
      campaignId={campaignId}
      characterId={charId}
      characterName={charName}
      onLeave={() => {
        setCharId(null);
        setCharName("");
        onNavigateHome();
      }}
    />
  );
}
