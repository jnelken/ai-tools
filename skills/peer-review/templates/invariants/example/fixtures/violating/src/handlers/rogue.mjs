// A nested entry point that bypasses the owner with an extensionless import.
import { createRawClient } from '../raw-client';
export const rogue = createRawClient();
