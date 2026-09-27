import {
  dailyActivityAggregatedCall,
  dailyActivityExportCall,
  dailyActivityKeySearchCall,
  dailyActivityModelTopKeysCall,
} from "@/components/networking";
import type { EntityType } from "@/components/EntityUsageExport/types";
import type {
  DailyActivityAggregatedResponse,
  DailyActivityEntity,
  DailyActivityKeySearchResponse,
  DailyActivityRequest,
  ExportFormat,
  ExportType,
  ModelTopKeysResponse,
} from "@/components/UsagePage/dailyActivityApi";

export interface EntityApi {
  aggregated(req: DailyActivityRequest): Promise<DailyActivityAggregatedResponse>;
  searchKeys(req: DailyActivityRequest, search: string): Promise<DailyActivityKeySearchResponse>;
  modelTopKeys(req: DailyActivityRequest, model: string, byModelGroup: boolean): Promise<ModelTopKeysResponse>;
  exportRows(req: DailyActivityRequest, exportType: ExportType, format: ExportFormat): Promise<Blob>;
}

const entityApi = (
  entity: DailyActivityEntity,
  defaults?: Pick<DailyActivityRequest, "excludeEntityIds">,
): EntityApi => ({
  aggregated: (req) => dailyActivityAggregatedCall(entity, { ...defaults, ...req }),
  searchKeys: (req, search) => dailyActivityKeySearchCall(entity, { ...defaults, ...req }, search),
  modelTopKeys: (req, model, byModelGroup) =>
    dailyActivityModelTopKeysCall(entity, { ...defaults, ...req }, model, byModelGroup),
  exportRows: (req, exportType, format) => dailyActivityExportCall(entity, { ...defaults, ...req }, exportType, format),
});

export const ENTITY_API: Record<EntityType, EntityApi> = {
  tag: entityApi("tag"),
  team: entityApi("team", { excludeEntityIds: ["litellm-dashboard"] }),
  organization: entityApi("organization"),
  customer: entityApi("customer"),
  agent: entityApi("agent"),
  user: entityApi("user"),
};
