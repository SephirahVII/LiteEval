"""Read-only SQL over currently published CSVs, never over platform storage."""
import csv
import io
import re
import sqlite3


class PublicView:
    def __init__(self,files,release_times):
        self.files=files
        self.release_times=release_times

    def tables(self,now):
        return {name[:-4].replace('/','_').replace('-','_'):text for name,text in self.files.items()
                if name.endswith('.csv') and self.release_times.get(name,0)<=now}

    @staticmethod
    def parsed(text):
        reader=csv.DictReader(io.StringIO(text))
        columns=reader.fieldnames or []
        rows=list(reader)
        types={c:('INTEGER' if not c.endswith('_id') and rows and all(re.fullmatch('-?[0-9]+',str(r[c] or '')) for r in rows) else 'TEXT') for c in columns}
        return columns,rows,types

    def query(self,query,now):
        if not isinstance(query,str) or not query.strip() or len(query)>16000:
            raise ValueError('SQL must be a nonempty string of at most16000 characters')
        connection=sqlite3.connect(':memory:')
        try:
            for name,text in self.tables(now).items():
                if not re.fullmatch('[A-Za-z_][A-Za-z0-9_]*',name):
                    continue
                columns,rows,types=self.parsed(text)
                if not columns or any(not re.fullmatch('[A-Za-z_][A-Za-z0-9_]*',c) for c in columns):
                    continue
                connection.execute(f'CREATE TABLE "{name}" ('+','.join('"'+c+'" '+types[c] for c in columns)+')')
                connection.executemany(f'INSERT INTO "{name}" VALUES ('+','.join('?' for _ in columns)+')',
                                       [tuple(row[c] for c in columns) for row in rows])
            allowed={sqlite3.SQLITE_SELECT,sqlite3.SQLITE_READ,sqlite3.SQLITE_FUNCTION}
            if hasattr(sqlite3,'SQLITE_RECURSIVE'):
                allowed.add(sqlite3.SQLITE_RECURSIVE)
            def authorize(action,arg1,arg2,database,source):
                if action==sqlite3.SQLITE_FUNCTION and str(arg2 or arg1).lower() in ('load_extension','readfile','writefile'):
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY
            connection.set_authorizer(authorize)
            steps=0
            def progress():
                nonlocal steps
                steps+=1000
                return int(steps>500000)
            connection.set_progress_handler(progress,1000)
            cursor=connection.execute(query)
            columns=[c[0] for c in cursor.description or []]
            rows=cursor.fetchmany(101)
            return {'columns':columns,'rows':[list(r) for r in rows[:100]],'truncated':len(rows)>100}
        except sqlite3.Error as exc:
            raise ValueError('SQL rejected: '+str(exc)) from None
        finally:
            connection.close()

    def catalog(self,now):
        return [{'table':name,'columns':self.parsed(text)[0],'types':self.parsed(text)[2]} for name,text in self.tables(now).items()]
