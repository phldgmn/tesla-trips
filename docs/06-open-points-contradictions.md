# Offene Punkte und strukturelle Spannungen im Plan

Diese Punkte sind keine reinen Formfehler, sondern beeinflussen, wie Module geschnitten und Schnittstellen entworfen werden müssen. Sie sollten vor bzw. während der Implementierung der jeweils betroffenen Module bewusst entschieden werden.

## 1. Zirkuläre Abhängigkeit: ETA ↔ Wetter ↔ Energie/Ladeplan

Die Wetterabfrage benötigt die erwartete Durchfahrtszeit an jedem Streckenpunkt. Diese Zeit hängt aber vom Energieverbrauch und der Ladeplanung ab — die wiederum vom Wetter abhängen. Es handelt sich um eine echte zirkuläre Abhängigkeit, kein triviales Detail.

**Lösung im vorliegenden Dokumentenset:** iterative Zwei-Phasen-Berechnung mit Konvergenzschwelle (siehe `02-architektur.md`, Abschnitt „Iterative Zeit-/Wetterauflösung"). Zu klären: konkreter Schwellwert für eine Nach-Iteration und maximale Iterationszahl — aktuell als konfigurierbarer Parameter vorgesehen, kein fixer Wert im Plan verankert. => Das ist ein guter erster Ansatz

## 2. Straßenroute wird unabhängig vom Energieverbrauch fixiert

GraphHopper berechnet die Route nach klassischen Kriterien (Zeit/Distanz/Weighting), bevor irgendeine Energie- oder Ladeinformation existiert. Die eigentliche „Reiseoptimierung" (Ladeplanung) kann diese Route anschließend nicht mehr verändern — sie plant nur noch Ladehalte auf dem bereits fixierten Straßenverlauf.

**Konsequenz:** Sollte eine alternative, leicht längere Straßenroute energetisch günstiger sein (z. B. weniger Steigung, günstigerer Wind), findet das System das nicht automatisch. Das ist keine unlösbare Inkonsistenz, aber eine bewusste Einschränkung des aktuellen Scopes, die im Dokumentenset jetzt explizit benannt ist (siehe `02-architektur.md`). Eine spätere Erweiterung um mehrere GraphHopper-Routenalternativen, aus denen die Optimierungsschicht die energetisch günstigste auswählt, wäre ein sauberer, nicht-invasiver Ausbauschritt — sofern das gewünscht ist, sollte das jetzt als Designentscheidung festgehalten werden, nicht erst bei der Implementierung entdeckt werden. => bitte den Ausbauschritt als spätere Option im Hinterkopf behalten, aber noch nicht implementieren, da er die Komplexität deutlich erhöht und die aktuelle Zielsetzung nicht zwingend erfordert.

## 3. Datenherkunft „Tesla Supercharger" ist nicht spezifiziert

Der Plan legt fest, dass ausschließlich Tesla Supercharger berücksichtigt werden — offen ist, **woher** diese Daten technisch bezogen werden. Optionen mit unterschiedlichen Implikationen:

- Inoffizielle/Community-APIs, die Tesla-Standortdaten aus der offiziellen Tesla-Website/App extrahieren (rechtlich/stabilitätsseitig unsicher, aber verbreitet in ABRP & Co.)
- Statischer, manuell gepflegter Datensatz (stabil, aber pflegeaufwändig, veraltet schnell)
- Gefilterte Ansicht auf OpenChargeMap, beschränkt auf Einträge mit Betreiber „Tesla" (wurde ursprünglich als generische Quelle erwähnt, aber laut aktueller Vorgabe nicht mehr vorgesehen, da explizit *nur* Tesla-Ladesäulen gewünscht sind, nicht „auch OpenChargeMap gefiltert auf Tesla")

Das `charging_infrastructure`-Modul ist bewusst hinter einem Provider-Interface gekapselt, damit diese Entscheidung die übrige Architektur nicht berührt — die konkrete Quelle sollte aber vor Implementierung dieses Moduls festgelegt werden. => Für Tesla-Ladesäulen wird in einer Ausbaustufe ein Crawler als eine Art "Plugin"/Modul eingebaut, annahme ist solange, dass die Daten dazu lokal vorliegen (was sie später auch tun werden, nur eben mit Crawler zur Sammlung).

## 4. Verhältnis Zwischenstopp ↔ Ladestopp ist im ursprünglichen Text nicht eindeutig

„Zwischenstopps" wurden als neue Anforderung ergänzt, ohne dass ursprünglich definiert war, ob damit (a) beliebige Pflicht-Wegpunkte (z. B. ein Besuch, eine Übernachtung) oder (b) eine alternative Bezeichnung für Ladestopps gemeint sind. Im vorliegenden Dokumentenset wurde Variante (a) angenommen: Zwischenstopps sind eigenständige, optional mit Aufenthaltsdauer versehene Pflicht-Wegpunkte, die unabhängig von der Ladeplanung existieren, aber ggf. mit einem Ladehalt zusammenfallen können (siehe `01-projektspezifikation.md`, Abschnitt „Zwischenstopps"). Diese Annahme sollte bestätigt werden, bevor die Optimierungsschicht (`optimization`-Modul) implementiert wird, da sie direkt die Zustandsraum-Modellierung betrifft. => Option (a) ist korrekt.

## 5. Kalibrierbarkeit vs. „keine Spekulation über Zukunft"

Die ursprüngliche Zielsetzung nennt explizit, dass das Verbrauchsmodell später aus eigenen Fahrdaten kalibriert werden soll. Das ist inhaltlich eine Aussage über zukünftige Nutzung, aber zugleich eine **Architekturanforderung an das jetzige Energiemodul** (Trennung von Modellstruktur und Parametern). Sie wurde daher nicht als spekulatives Zukunftsfeature entfernt, sondern als Design-Constraint für das `energy`-Modul beibehalten (siehe `03-modulspezifikationen.md`, Modul 6). Falls das nicht gewünscht ist und das Energiemodul auch mit fest verdrahteten Parametern starten darf, wäre das eine bewusste Vereinfachung, die explizit gegen diese Vorgabe entschieden werden müsste. => Es darf auh mit fest verdrahteten Parametern gestartet werden, die Kalibrierbarkeit ist optional.

## 6. Baustellendaten und Planungsvorlauf

Bei Reiseplanung mit größerem zeitlichem Vorlauf (z. B. mehrere Tage vor Abfahrt) spiegeln aktuelle DATEX-II-Baustellendaten nicht zwingend den Zustand zum tatsächlichen Reisezeitpunkt wider. Das ist keine strukturelle Inkonsistenz, sondern eine inhärente Grenze der Datenquelle — sollte aber in der Optimierung als Unsicherheit behandelt werden (z. B. über die ohnehin vorgesehene Sicherheitsreserve), nicht als verlässliche Punktinformation. => Das ist ja ein generelles Problem, das ALLE Daten betrifft — Wetter, Batteriedegradation, Baustellen, Verkehrslage. Es ist nicht möglich, dass die Optimierung alle diese Unsicherheiten berücksichtigt, daher wird nur eine Sicherheitsreserve für die Batterie berücksichtigt.

## Empfehlung

Punkte 3 und 4 sollten vor dem Start der jeweils betroffenen Modul-Implementierung (`charging_infrastructure` bzw. `optimization`) explizit entschieden werden — beide beeinflussen Schnittstellen, die später nur mit Mehraufwand geändert werden können. Punkt 2 ist keine Blockade für den Start, sollte aber als bewusste Scope-Entscheidung dokumentiert bleiben, damit sie später nicht als Bug missverstanden wird.
