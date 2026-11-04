import { useMemo } from "react";
import type { CellValueChangedEvent, ColDef } from "ag-grid-community";
import { AgGridReact } from "ag-grid-react";

import type { HsdsRecord } from "../../api/client";
import { fieldLabel } from "./queue";

const EDITABLE_FIELDS = [
  "name",
  "status",
  "description",
  "eligibility_description",
  "minimum_age",
  "maximum_age",
  "application_process",
  "fees_description",
  "wait_time",
  "email",
  "url",
];

interface FieldRow {
  key: string;
  label: string;
  value: string;
}

interface FieldsEditorProps {
  record: HsdsRecord;
  onChange(field: string, value: unknown): void;
}

function parseValue(field: string, text: string | null | undefined): unknown {
  const trimmed = text?.trim() ?? "";
  if (!trimmed) return null;
  if (field === "minimum_age" || field === "maximum_age") {
    const number = Number(trimmed);
    return Number.isNaN(number) ? trimmed : number;
  }
  return trimmed;
}

export function FieldsEditor({ record, onChange }: FieldsEditorProps) {
  const rows = useMemo<FieldRow[]>(
    () =>
      EDITABLE_FIELDS.map((key) => ({
        key,
        label: fieldLabel(key),
        value: record[key] === undefined ? "" : String(record[key]),
      })),
    [record],
  );
  const columns = useMemo<ColDef<FieldRow>[]>(
    () => [
      { field: "label", headerName: "Field", width: 170, sortable: false },
      {
        field: "value",
        headerName: "Value (double-click to edit)",
        flex: 1,
        editable: true,
        sortable: false,
        wrapText: true,
        autoHeight: true,
        cellEditor: "agLargeTextCellEditor",
        cellEditorPopup: true,
      },
    ],
    [],
  );
  return (
    <div className="ag-theme-quartz fields-grid">
      <AgGridReact<FieldRow>
        rowData={rows}
        columnDefs={columns}
        domLayout="autoHeight"
        getRowId={(params) => params.data.key}
        suppressMovableColumns
        stopEditingWhenCellsLoseFocus
        onCellValueChanged={(event: CellValueChangedEvent<FieldRow>) => {
          if (event.data) onChange(event.data.key, parseValue(event.data.key, event.newValue));
        }}
      />
    </div>
  );
}
