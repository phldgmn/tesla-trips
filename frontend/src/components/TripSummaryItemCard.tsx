import type {
  ChargingCostByCurrency,
  ChargingStop,
  FerrySegment,
} from "@/types";
import {
  Banknote,
  Car,
  EvCharger,
  Hourglass,
  RotateCwFadingClock,
  Route,
  Ship,
  type LucideIcon,
} from "lucide-react";
import { Popover } from "./Popover";
import { formatChargingCosts, formatKm, formatMinutes } from "./TripSummary";
import { formatCost } from "@/utils/currency-utils";

export enum TripSummaryItemType {
  Distance,
  ChargingTime,
  DrivingTime,
  TravelTime,
  WaitingTime,
  Ferries,
  Cost,
}

export interface ChargingCostBreakdownItem {
  currency: string;
  originalAmount: number;
  eurAmount: number;
}

export interface ChargingCostInfo {
  eurTotal: number | null;
  breakdown: ChargingCostBreakdownItem[];
  countMissingPricing: number;
  stops: ChargingStop[];
}

export interface TripSummaryItemCardProps {
  type: TripSummaryItemType;
  value: number | ChargingCostInfo | FerrySegment[];
}

export function TripSummaryItemCard({ type, value }: TripSummaryItemCardProps) {
  switch (type) {
    case TripSummaryItemType.Distance:
      return (
        <_TripSummaryItemCard
          Icon={Route}
          title="Distanz"
          value={formatKm(value as number)}
        />
      );
    case TripSummaryItemType.ChargingTime:
      return (
        <_TripSummaryItemCard
          Icon={EvCharger}
          title="Ladezeit"
          value={formatMinutes(value as number)}
        />
      );
    case TripSummaryItemType.DrivingTime:
      return (
        <_TripSummaryItemCard
          Icon={Car}
          title="Fahrzeit"
          value={formatMinutes(value as number)}
        />
      );
    case TripSummaryItemType.TravelTime:
      return (
        <_TripSummaryItemCard
          Icon={RotateCwFadingClock}
          title="Reisezeit"
          value={formatMinutes(value as number)}
        />
      );
    case TripSummaryItemType.WaitingTime:
      return (
        <_TripSummaryItemCard
          Icon={Hourglass}
          title="Wartezeit"
          value={formatMinutes(value as number)}
        />
      );
    case TripSummaryItemType.Ferries:
      return (
        <_TripSummaryItemCard
          Icon={Ship}
          title="Fähren"
          value={(value as FerrySegment[]).map((f) => f.name).join(", ")}
        />
      );
    case TripSummaryItemType.Cost:
      return (
        <_TripSummaryItemCard
          Icon={Banknote}
          title="Kosten"
          value={chargingCostInfo(value as ChargingCostInfo)}
        />
      );
  }

  interface _TripSummaryItemCardProps {
    Icon: LucideIcon;
    title: string;
    value: string | JSX.Element;
  }

  function _TripSummaryItemCard({
    Icon,
    title,
    value,
  }: _TripSummaryItemCardProps) {
    return (
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: "0.25rem",
          color: "#1f2937",
        }}
      >
        <Popover content={title}>
          <Icon size={20} strokeWidth={2} color="#1f2937" />
        </Popover>
        {value}
      </div>
    );
  }

  function toChargingCostByCurrency(
    breakdown: ChargingCostBreakdownItem[],
  ): ChargingCostByCurrency[] {
    return breakdown.map(
      (b) =>
        ({
          currency: b.currency,
          amount: b.originalAmount,
        }) as ChargingCostByCurrency,
    );
  }

  function chargingCostInfo(info: ChargingCostInfo): JSX.Element | string {
    if (info.eurTotal === null) {
      return formatChargingCosts(
        toChargingCostByCurrency(info.breakdown),
        info.countMissingPricing,
      );
    }

    return (
      <Popover
        content={
          <>
            <div style={{ fontWeight: 600, marginBottom: "0.25rem" }}>
              Summe: {formatCost(info.eurTotal, "EUR")}
            </div>

            {info.breakdown
              .filter((x) => x.currency === "EUR")
              .map((b) => (
                <div key={b.currency} style={{ textAlign: "right" }}>
                  {formatCost(b.eurAmount, "EUR")}
                </div>
              ))}
            {info.breakdown
              .filter((x) => x.currency !== "EUR")
              .map((b) => (
                <div
                  key={b.currency}
                  style={{
                    display: "grid",
                    gridTemplateColumns: "1fr auto 1fr",
                    rowGap: "0.25rem",
                    columnGap: "1rem",
                    gap: "1rem",
                    alignItems: "baseline",
                  }}
                >
                  <span style={{ textAlign: "right" }}>
                    {formatCost(b.originalAmount, b.currency)}
                  </span>
                  <span style={{ textAlign: "right" }}>=</span>
                  <span style={{ textAlign: "right" }}>
                    {formatCost(b.eurAmount, "EUR")}
                  </span>
                </div>
              ))}
            {info.countMissingPricing > 0 && (
              <div
                style={{
                  marginTop: "0.25rem",
                  fontSize: "0.75rem",
                  opacity: 0.7,
                }}
              >
                ({info.countMissingPricing} Halt
                {info.countMissingPricing === 1 ? "" : "e"} ohne Preisdaten)
              </div>
            )}
          </>
        }
      >
        <span
          style={{
            cursor: "help",
            textDecoration: "underline dotted",
          }}
        >
          {formatCost(info.eurTotal, "EUR")}
        </span>
      </Popover>
    );
  }
}
