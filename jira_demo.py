"""Fictional Jira launch-project records for exercising the in-app copilot."""
from datetime import date, timedelta


def demo_issues(today=None):
    today = today or date.today()
    # Stable IDs and relative dates keep overdue/upcoming scenarios usable later.
    examples = [
        ('PIL-101','Payment webhook retries fail after a timeout','Blocked','Highest','Priya Nair',-2,5,'Payments','Awaiting the payment sandbox credentials from the platform team.'),
        ('PIL-102','Apple Pay checkout loses the shipping address','Blocked','High','Priya Nair',2,3,'Checkout','Waiting for the mobile SDK patch before QA can reproduce the fix.'),
        ('PIL-103','Order confirmation emails arrive twice','In Progress','High','Sam Reed',-1,3,'Notifications',''),
        ('PIL-104','Inventory sync stops when the warehouse API rate limits','Blocked','Medium','Maya Chen',-4,8,'Inventory','Warehouse API test access is pending; the retry strategy needs vendor confirmation.'),
        ('PIL-105','Add keyboard navigation to the account menu','To Do','Medium','Alex Morgan',3,2,'Accessibility',''),
        ('PIL-106','Remove duplicate analytics events on checkout','Done','High','Priya Nair',-5,2,'Analytics',''),
        ('PIL-107','Investigate slow product search under load','In Review','Highest','Sam Reed',1,5,'Search',''),
        ('PIL-108','Update the onboarding help text','To Do','Low',None,5,1,'Onboarding',''),
        ('PIL-109','Export invoices for finance reconciliation','In Progress','High','Alex Morgan',-3,5,'Billing',''),
        ('PIL-110','Complete the launch security checklist','Blocked','Highest','Maya Chen',0,3,'Security','Security review is waiting for the final penetration-test report.'),
        ('PIL-111','Restore password reset links after expiry','Done','Medium','Sam Reed',-6,2,'Accounts',''),
        ('PIL-112','Write the launch rollback runbook','To Do','High',None,2,3,'Release',''),
    ]
    rows=[]
    for i,(key,summary,status,priority,assignee,due,points,component,reason) in enumerate(examples):
        rows.append({'key':key,'project':'PIL','type':'Bug' if i<7 else 'Task','summary':summary,
            'status':status,'priority':priority,'assignee':assignee,'reporter':'Alex Morgan',
            'sprint':'Launch Sprint','epic':'Pilant Demo Launch','story_points':points,
            'labels':[component.lower(),'demo-launch'],'components':[component],
            'fix_version':'Demo 1.0','due':(today+timedelta(days=due)).isoformat(),
            'created':(today-timedelta(days=12+i)).isoformat(),
            'updated':(today-timedelta(days=i%4)).isoformat(),'watchers':i%5+1,'comments':i%7,
            'description':f'Fictional test issue for the {component.lower()} workstream. No production customer data.',
            'blocked_reason':reason,'data_origin':'Fictional copilot demonstration'})
    return rows
