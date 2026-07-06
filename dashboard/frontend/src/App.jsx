import React from "react";
import StatusBar from "./components/StatusBar.jsx";
import HealthTiles from "./components/HealthTiles.jsx";
import RiskTiles from "./components/RiskTiles.jsx";
import LogTail from "./components/LogTail.jsx";

export default function App() {
  return (
    <div className="min-h-screen">
      <StatusBar />
      <main className="mx-auto flex max-w-6xl flex-col gap-6 p-4">
        <HealthTiles />
        <RiskTiles />
        <LogTail />
      </main>
    </div>
  );
}
