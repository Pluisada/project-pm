# Frontend Codebase Overview

## Technology Stack

- **Framework**: Next.js 16.1.6 (React 19.2.3)
- **Styling**: Tailwind CSS 4 with PostCSS
- **Drag & Drop**: @dnd-kit (v6.3.1 core + v10.0.0 sortable)
- **Testing**: Vitest (unit) + Playwright (E2E)
- **Type Safety**: TypeScript 5

## Project Structure

```
frontend/
├── src/
│   ├── app/
│   │   ├── layout.tsx       # Root layout with CSS variables and global styles
│   │   └── page.tsx         # Entry point, renders KanbanBoard
│   ├── components/
│   │   ├── KanbanBoard.tsx  # Main board component with state management
│   │   ├── KanbanColumn.tsx # Individual column with droppable area
│   │   ├── KanbanCard.tsx   # Individual card with drag handles
│   │   ├── KanbanCardPreview.tsx # Drag preview rendering
│   │   ├── NewCardForm.tsx  # Form to add new cards
│   │   └── KanbanBoard.test.tsx # Component unit tests
│   ├── lib/
│   │   ├── kanban.ts        # Core data types and logic (Card, Column, BoardData)
│   │   └── kanban.test.ts   # Unit tests for kanban logic
│   └── test/
│       ├── setup.ts         # Vitest configuration
│       └── vitest.d.ts      # Type definitions
├── tests/
│   └── kanban.spec.ts       # Playwright E2E tests
├── package.json             # Dependencies and scripts
├── tsconfig.json            # TypeScript configuration
├── next.config.ts           # Next.js configuration
├── vitest.config.ts         # Vitest configuration
└── playwright.config.ts     # Playwright configuration
```

## Core Components

### Data Model (lib/kanban.ts)

**Types:**
- `Card`: Has `id`, `title`, `details`
- `Column`: Has `id`, `title`, `cardIds[]`
- `BoardData`: Contains `columns[]` and `cards{}` (keyed by id)

**Utilities:**
- `initialData`: Sample board with 5 columns and 8 cards
- `moveCard(columns, activeId, overId)`: Complex drag-and-drop logic handling same/different column moves
- `createId(prefix)`: Generates unique IDs using random + timestamp
- `findColumnId(columns, id)`: Determines which column contains a card

### KanbanBoard Component

**State:**
- `board`: Full BoardData structure
- `activeCardId`: Currently dragging card (for DragOverlay)

**Handlers:**
- `handleDragStart/End`: Uses @dnd-kit with PointerSensor (6px activation constraint)
- `handleRenameColumn`: Edits column title
- `handleAddCard`: Creates new card with auto-generated ID
- `handleDeleteCard`: Removes card and reference from column

**Features:**
- Responsive grid layout (5 columns on large screens)
- Decorative gradient backgrounds (blue and purple)
- Shows card count per column
- Displays column pills in header

### KanbanColumn Component

**Props:**
- `column`, `cards[]`, callbacks: `onRename`, `onAddCard`, `onDeleteCard`

**Features:**
- Droppable zone with @dnd-kit
- Visual feedback when hovering (yellow ring)
- Editable column title
- Card count display
- Empty state with "Drop a card here" message
- NewCardForm at bottom

### KanbanCard Component

**Props:**
- `card`, `onDelete` callback

**Features:**
- Draggable with @dnd-kit
- Shows title and details
- Delete button
- Used in both main board and DragOverlay (via KanbanCardPreview)

### NewCardForm Component

**Functionality:**
- Input fields for title and details
- Submit button
- Resets after adding card

## Styling

Uses CSS custom properties defined in app/layout.tsx:
- `--primary-blue`: #209dd7
- `--secondary-purple`: #753991
- `--accent-yellow`: #ecad0a
- `--navy-dark`: #032147
- `--gray-text`: #888888
- `--stroke`: Light borders
- `--surface`: Light backgrounds
- `--surface-strong`: Stronger backgrounds
- `--shadow`: Consistent shadow style

## Testing

### Unit Tests (Vitest)
- Located in `src/**/*.test.ts(x)` or `src/test/`
- Tests for kanban logic (moveCard, createId)
- Component tests with React Testing Library

### E2E Tests (Playwright)
- Located in `tests/kanban.spec.ts`
- Tests user interactions (drag, drop, edit, delete)
- Tests visual states and responsiveness

## Current Limitations

1. No backend persistence - state resets on page refresh
2. No user authentication
3. No AI integration
4. Frontend-only, runs in development mode
5. Not configured for Docker deployment yet

## How to Run

```bash
# Install dependencies
npm install

# Development server
npm run dev  # http://localhost:3000

# Run tests
npm run test:unit      # Vitest
npm run test:e2e       # Playwright
npm run test:all       # Both

# Build for production
npm run build
npm start
```

## Next Steps (Per PLAN.md)

- Part 2: Set up FastAPI backend and Docker infrastructure
- Part 3: Integrate static build into backend
- Part 4: Add authentication layer
- Part 5-10: Database, persistence, AI integration
