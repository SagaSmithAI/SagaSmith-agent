import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowLeft, Send, Users } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useClient } from "@/providers/ClientProvider";
import type { DndPeerMessage, DndRoomPlayer } from "@/lib/types";

interface Props {
  campaignId: string;
  characterId: string;
  characterName: string;
  onLeave: () => void;
}

export function RoomView({ campaignId, characterId, characterName, onLeave }: Props) {
  const { client } = useClient();
  const [players, setPlayers] = useState<DndRoomPlayer[]>([]);
  const [messages, setMessages] = useState<DndPeerMessage[]>([]);
  const [input, setInput] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Subscribe to room events
  useEffect(() => {
    if (!client) return;

    const unsub = client.onChat(campaignId, (event: any) => {
      if (event.event === "peer_message" && event.chat_id === campaignId) {
        setMessages((prev) => [...prev, event as DndPeerMessage]);
        setTimeout(() => scrollRef.current?.scrollTo(0, scrollRef.current.scrollHeight), 50);
      } else if (event.event === "room_presence" && event.chat_id === campaignId) {
        setPlayers(event.players || []);
      }
    });

    // Join room
    client.sendRaw({
      type: "room_join",
      chat_id: campaignId,
      player: { character_id: characterId, name: characterName },
    });

    return unsub;
  }, [client, campaignId, characterId, characterName]);

  const sendPeer = useCallback(() => {
    if (!input.trim() || !client) return;
    client.sendRaw({
      type: "peer_message",
      chat_id: campaignId,
      content: input,
      player: { character_id: characterId, name: characterName },
      timestamp: new Date().toISOString(),
    });
    setInput("");
    inputRef.current?.focus();
  }, [client, campaignId, characterId, characterName, input]);

  const sendDm = useCallback(() => {
    if (!input.trim() || !client) return;
    client.sendMessage(campaignId, input);
    setInput("");
    inputRef.current?.focus();
  }, [client, campaignId, input]);

  const handleKey = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (input.startsWith("@DM") || input.startsWith("@dm")) {
        sendDm();
      } else {
        sendPeer();
      }
    }
  };

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-border">
        <div className="flex items-center gap-3">
          <button onClick={onLeave} className="text-muted-foreground hover:text-foreground">
            <ArrowLeft className="w-4 h-4" />
          </button>
          <div>
            <span className="font-medium">Campaign Room</span>
            <span className="ml-2 text-xs text-muted-foreground">Playing as {characterName}</span>
          </div>
        </div>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Users className="w-3.5 h-3.5" />
          <span>{players.length} online</span>
        </div>
      </div>

      {/* Main content */}
      <div className="flex-1 flex">
        {/* Messages */}
        <div ref={scrollRef} className="flex-1 overflow-auto p-4 space-y-2">
          {messages.length === 0 && (
            <p className="text-muted-foreground text-center py-8 text-sm">
              No messages yet. Say something, or @DM to talk to the dungeon master.
            </p>
          )}
          {messages.map((msg, i) => (
            <div key={i} className="flex gap-2 text-sm">
              <span className="font-medium text-primary shrink-0">{msg.player.name}:</span>
              <span className="text-foreground whitespace-pre-wrap">{msg.text}</span>
            </div>
          ))}
        </div>

        {/* Player list sidebar */}
        <div className="w-48 border-l border-border p-3 hidden lg:block">
          <h4 className="text-xs font-medium text-muted-foreground mb-2 uppercase">Online</h4>
          {players.map((p) => (
            <div key={p.character_id} className="flex items-center gap-2 py-1 text-sm">
              <span className={`w-2 h-2 rounded-full ${p.character_id === characterId ? "bg-green-500" : "bg-blue-400"}`} />
              <span>{p.name}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Input */}
      <div className="border-t border-border p-3">
        <div className="flex gap-2">
          <input
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKey}
            placeholder="Say something... (use @DM to talk to the dungeon master)"
            className="flex-1 rounded-md border border-border bg-background px-3 py-2 text-sm"
          />
          <Button size="sm" onClick={input.startsWith("@DM") ? sendDm : sendPeer} disabled={!input.trim()}>
            <Send className="w-4 h-4" />
          </Button>
        </div>
        <p className="text-xs text-muted-foreground mt-1">Enter = send to party · @DM = send to dungeon master</p>
      </div>
    </div>
  );
}
