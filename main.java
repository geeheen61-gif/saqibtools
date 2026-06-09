import java.util.ArrayList;
import java.util.Scanner;

// ============================================================
//  DEAL MANAGEMENT SYSTEM - Java OOP (Beginner Friendly)
//  Concepts used: Class, Object, Constructor, Methods, ArrayList
// ============================================================

// -------------------------------------------------------
// 1. Deal CLASS  →  Blueprint for one deal
// -------------------------------------------------------
class Deal {

    // Properties (what a deal HAS)
    int    id;
    String title;
    String clientName;
    double amount;
    String status;   // "Open", "Won", "Lost"

    // Constructor → runs when we create a new Deal object
    Deal(int id, String title, String clientName, double amount) {
        this.id         = id;
        this.title      = title;
        this.clientName = clientName;
        this.amount     = amount;
        this.status     = "Open";   // every new deal starts as Open
    }

    // Method → show deal details
    void display() {
        System.out.println("------------------------------------------");
        System.out.println("  Deal ID    : " + id);
        System.out.println("  Title      : " + title);
        System.out.println("  Client     : " + clientName);
        System.out.println("  Amount     : $" + amount);
        System.out.println("  Status     : " + status);
        System.out.println("------------------------------------------");
    }

    // Method → update status
    void updateStatus(String newStatus) {
        this.status = newStatus;
        System.out.println("✅ Deal '" + title + "' status updated to: " + newStatus);
    }
}

// -------------------------------------------------------
// 2. DealManager CLASS  →  Manages a list of deals
// -------------------------------------------------------
class DealManager {

    // ArrayList stores all our Deal objects
    ArrayList<Deal> deals = new ArrayList<>();
    int nextId = 1;   // auto-increment ID counter

    // Method → Add a new deal
    void addDeal(String title, String clientName, double amount) {
        Deal newDeal = new Deal(nextId, title, clientName, amount);
        deals.add(newDeal);
        System.out.println("🎉 Deal added! ID: " + nextId);
        nextId++;
    }

    // Method → View all deals
    void viewAllDeals() {
        if (deals.isEmpty()) {
            System.out.println("⚠️  No deals found.");
            return;
        }
        System.out.println("\n========== ALL DEALS ==========");
        for (Deal d : deals) {      // loop through every deal
            d.display();
        }
    }

    // Method → Find deal by ID
    Deal findDealById(int id) {
        for (Deal d : deals) {
            if (d.id == id) {
                return d;   // found it!
            }
        }
        return null;   // not found
    }

    // Method → Update deal status
    void updateDealStatus(int id, String newStatus) {
        Deal d = findDealById(id);
        if (d != null) {
            d.updateStatus(newStatus);
        } else {
            System.out.println("❌ Deal with ID " + id + " not found.");
        }
    }

    // Method → Delete a deal
    void deleteDeal(int id) {
        Deal d = findDealById(id);
        if (d != null) {
            deals.remove(d);
            System.out.println("🗑️  Deal '" + d.title + "' deleted.");
        } else {
            System.out.println("❌ Deal with ID " + id + " not found.");
        }
    }

    // Method → Show summary (total deals, total value)
    void showSummary() {
        double total = 0;
        int won = 0, lost = 0, open = 0;

        for (Deal d : deals) {
            total += d.amount;
            if (d.status.equals("Won"))  won++;
            if (d.status.equals("Lost")) lost++;
            if (d.status.equals("Open")) open++;
        }

        System.out.println("\n========== SUMMARY ==========");
        System.out.println("  Total Deals  : " + deals.size());
        System.out.println("  Open         : " + open);
        System.out.println("  Won          : " + won);
        System.out.println("  Lost         : " + lost);
        System.out.println("  Total Value  : $" + total);
        System.out.println("==============================");
    }
}

// -------------------------------------------------------
// 3. MAIN CLASS  →  Entry point, shows menu
// -------------------------------------------------------
public class Main {

    public static void main(String[] args) {

        Scanner sc = new Scanner(System.in);
        DealManager manager = new DealManager();

        System.out.println("====================================");
        System.out.println("   DEAL MANAGEMENT SYSTEM (Java)   ");
        System.out.println("====================================");

        // Add some sample deals to start with
        manager.addDeal("Website Project",  "Ali Khan",    5000.0);
        manager.addDeal("Mobile App",       "Sara Ahmed",  8000.0);
        manager.addDeal("SEO Package",      "Usman Corp",  1500.0);

        boolean running = true;

        while (running) {
            // Show menu
            System.out.println("\n-------- MENU --------");
            System.out.println("1. View All Deals");
            System.out.println("2. Add New Deal");
            System.out.println("3. Update Deal Status");
            System.out.println("4. Delete a Deal");
            System.out.println("5. Show Summary");
            System.out.println("6. Exit");
            System.out.print("Choose option (1-6): ");

            int choice = sc.nextInt();
            sc.nextLine();   // clear leftover newline

            switch (choice) {

                case 1:
                    manager.viewAllDeals();
                    break;

                case 2:
                    System.out.print("Enter deal title   : ");
                    String title = sc.nextLine();
                    System.out.print("Enter client name  : ");
                    String client = sc.nextLine();
                    System.out.print("Enter deal amount  : ");
                    double amount = sc.nextDouble();
                    sc.nextLine();
                    manager.addDeal(title, client, amount);
                    break;

                case 3:
                    System.out.print("Enter Deal ID      : ");
                    int updateId = sc.nextInt();
                    sc.nextLine();
                    System.out.println("New status options : Open / Won / Lost");
                    System.out.print("Enter new status   : ");
                    String status = sc.nextLine();
                    manager.updateDealStatus(updateId, status);
                    break;

                case 4:
                    System.out.print("Enter Deal ID to delete: ");
                    int deleteId = sc.nextInt();
                    sc.nextLine();
                    manager.deleteDeal(deleteId);
                    break;

                case 5:
                    manager.showSummary();
                    break;

                case 6:
                    System.out.println("👋 Goodbye! Keep closing deals!");
                    running = false;
                    break;

                default:
                    System.out.println("⚠️  Invalid option. Try again.");
            }
        }

        sc.close();
    }
}