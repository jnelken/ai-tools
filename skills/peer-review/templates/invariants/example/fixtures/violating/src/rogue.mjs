// A new path that bypasses the owner: the guard must fail on it.
import { createRawClient } from './raw-client.mjs';
export const rogue = createRawClient();
